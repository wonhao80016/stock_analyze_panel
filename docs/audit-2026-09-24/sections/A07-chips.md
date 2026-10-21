# A07-chips｜三大法人、融資融券與籌碼因子

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 5 | 2 | 0 | 0 | 0 | 17 | 6 |

## 稽核者摘要

欄位對應與單位都正確。把官方原始 JSON 餵給四個 adapter（T86、TPEx dailyTrade、MI_MARGN、TPEx margin/balance），兩天共 16,692 列，不一致 0 筆。融資融券 store 與官方 9/22、9/23 全部一致（包含餘額、增減與券資比），也和 Yahoo 奇摩股市的 0050（10 日）、2454（2 日）逐值相符。
有 3 個實質問題：
(1) [P1] 三大法人在 16:30 寫入的是初版資料。官方之後仍會補鉅額交易等更正，但同一日期一旦寫入就永遠不再重抓。9/22、9/23 上市各有 27、28 檔與官方終版不同，上櫃各 10、7 檔；投信差距最大 372.5 萬股。自選股 2454 在 9/22 的投信 store 為 216 張，官方與 Yahoo 為 373 張；9/17 為 -41 張，Yahoo 為 +37 張，正負號相反；5 日投信累計少算約 40%。
(2) [P2] 個股詳細頁的即時抓取路徑永遠不計算券資比。只要即時抓取成功，「券資比」與「最新券資比因子」一律顯示「--」。
(3) [P2] 市場層級的法人買賣超用「股數加總」，而且上櫃含權證、上市不含。9/23 投信顯示 +20,233 張（買超），官方是賣超 42.16 億元，方向相反。
另有 4 項 P3：factors.py 是未使用且語意有誤的程式；即時路徑遇到當日 T86 無該股時會退回前一日；選股器沿用舊列；「近 5 日指標」卡片裡有 1 日值。
網路請求：WebSearch 4 次、WebFetch 9 次，都沒有連 twse、tpex。本次沒有修改 repo 或 data。

## 問題清單

### [P1 → P1] 三大法人在 16:30 寫入初版 T86 與上櫃資料後不再重抓，官方後續修正（含鉅額交易）永遠不會入庫（確認成立）

- **主張**：每日 16:30 排程抓當日法人資料並寫入分區。21:40 那班與之後的回補，都會因為「該日已有分區」而略過。官方在 16:30 之後仍會修正部分個股的外資與投信數字，store 因此永久保留初版值。
- **正確的定義或數值**：入庫的法人買賣超應與官方終版一致，例如 2454 在 9/22 的投信為 373 張、合計 1,972 張，第三方網站也顯示相同數字。
- **app 實際行為**：store 永久保存 16:30 的初版：2454 在 9/22 的投信為 216 張、合計 1,815 張，9/17 的投信方向相反；5 日因子、歷史表、研究脈絡與市場彙總都沿用這些錯誤值。
- **對使用者的影響**：個股詳細頁的三大法人卡（store 備援時）、歷史視窗、「外資／投信／自營商 5 日累計」因子、AI 研究脈絡與全市場法人統計，約 2% 的個股會長期與官方及券商網站不一致。自選的 2454 已受影響（投信 5 日累計少算約 40%，單日方向相反）；會做鉅額交易的大型股受影響機率最高。
- **修法建議**：在 21:40 班（或次一交易日）以 force 重抓最近 1～2 個交易日的 T86 與上櫃法人資料並覆寫；或把法人抓取移到 17:30 之後。另外建議只在上市與上櫃都有資料時才寫入分區，並補一個「重抓後覆寫前一版」的回歸測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/jobs/daily_pipeline.py:177-180（16:30 抓日線與法人）
  - backend/app/jobs/daily_pipeline.py:194-195（註解：「已落盤的日期會被 refresh 略過, 不重複下載」）
  - backend/app/taiwan/daily_update.py:324-328、124-147（store 最新日期已等於目標日時回傳 None，備註為 Institutional store already current）
  - backend/app/taiwan/institutional_margin_refresh.py:143、166-169（available 集合中的日期一律略過）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW/institutional-trading：2026/09/22 投信 373 張、合計 1,972 張；9/17 投信 37 張、合計 420 張；9/16 投信 -483 張、合計 729 張
  - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm：鉅額「逐筆交易 9：00至17：00」；「上櫃 投信及外資 17:30」
  - T86 官方附註（reference/raw/twse_t86_20260922.json 的 notes）：「本統計資訊含一般、零股、盤後定價、鉅額，不含拍賣、標購。」
- **本地計算證據**：
  - store 分區的修改時間：9/22 為 16:30:07，9/23 為 16:30:09。2026-08 到 09 月間在交易日當天寫入的法人分區有 9/16、9/17、9/18、9/22、9/23
  - store 對官方終版（9/24 01:50 取得）：9/22 上市 27/1,332 列不同、上櫃 10/6,146 列；9/23 上市 28/1,329 列、上櫃 7/5,670 列。投信淨額最大差 3,725,000 股（3481 群創 9/23）。合計方向相反的例子：8028 在 9/22 為 -2,969,992 對 +37,008；2383 為 211,866 對 -272,574；3605 為 -605,007 對 1,461,993（見 inst_diff.out）
  - 自選股 2454 在 9/22：投信 store 216,171 股，官方 373,171 股；合計 1,815,448 股，官方 1,972,448 股
  - 以 Yahoo 比對 2454、3034、6196 共 136 個值：只有 2454 在 9/16、9/17、9/22（都是當日寫入的分區）出現差異；9/17 的投信 store 為 -40,815 股，Yahoo 為 +37 張，正負號相反。9/21 是事後寫入的分區，與 Yahoo 完全相符
  - 2454 在 9/23 的 5 日投信累計：app 為 350,309 股，Yahoo 五日加總為 585 張，少算約 40%
  - 市場層級上市投信加總：9/22 store 為 11,836 張，官方終版為 16,544 張
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：每日 16:30 排程抓當日 T86 與上櫃法人資料並寫入分區。21:40 班與回補 API 都沒有 force，看到當日已有分區就略過；9/23 21:40 有執行，但分區 mtime 仍停在 16:30:09。官方會在初次公布後補計盤後定價與鉅額成交，上市投信的更新時間約在 20:30～21:00，FinMind 也建議在交易日後 1～2 個營業日再取定案值。因此 store 會永久保留初版值。

以 9/24 01:5x 的官方版本比對：
- 淨額不同的列數：9/22 上市 17、上櫃 5；9/23 上市 17、上櫃 7，幾乎都是投信。若把買、賣量也算進去，則是 27/10 與 28/7。
- 例子：2454 在 9/22 的投信 216,171 股對 373,171 股；9/17 的正負號相反；9/23 的五日投信累計少算約 40%。
- 上市投信全市場加總偏差 13～28%。

修法應在 21:40 班 force 重抓當日資料，或在次一、二個交易日重抓並覆寫。只把抓取時間移到 17:30 不夠。
    - 理由：程式碼、本地資料、log 與兩個跟 finding 不同的外部來源，都支持這條 finding 的主張。

一、程式碼路徑（依現況逐行讀過）
- 16:30 班抓日線與法人；21:40 班雖然也設 refresh_institutional=True，但沒有傳 force。
- 進入 run_update 後，i_max 已等於 target，resolve_missing_date_range 回傳 None，於是記下 "Institutional store already current"，不會呼叫 refresh_dates。
- 就算呼叫到 refresh_dates，available 集合裡的日期也一律略過。
- 回補 API（enrichment_backfill）同樣不帶 force。能覆寫的只剩 CLI 的 --force。
- 模組 docstring 明寫設計原則是「0 rewrite of existing partitions」。

二、實際發生過
- 9/23 21:40 班確實執行：log 記錄 inst=success。但 date=2026-09-23 分區的 mtime 仍是 16:30:09，可見沒有重寫。

三、數值自行重算（diff.py、diff2.py）
- 以 9/24 01:5x 下載的官方資料比對 store。
- 買、賣、淨任一欄不同的列數：9/22 上市 27、上櫃 10；9/23 上市 28、上櫃 7。與 finding 完全相同。
- 只看淨額欄，差異列數：9/22 上市 17、上櫃 5；9/23 上市 17、上櫃 7。其中幾乎都是投信（9/22 有 21 列、9/23 有 24 列），外資只有 9/22 兩列。
- 2454 在 9/22：
  - 投信買進 289,920 → 446,920（+157,000），賣出不變，因此淨額 216,171 → 373,171。
  - 外資買賣各 +68,000，淨額不變。
- 8028、2383、3605 的合計正負相反，3481 的投信差 3,725,000 股，全部重現。
- 上市投信全市場加總：9/22 為 11,836,489 對 16,543,539 股（少約 28%）；9/23 為 24,292,839 對 21,397,398 股（多約 13.5%）。

四、第三方來源（HiStock，與 finding 用的 Yahoo 不同）
- 2454 的 9/22（投信 373、合計 1,972）、9/17（投信 37、合計 420）、9/16（投信 -483、合計 729）都與 store 不同。store 在 9/17 的投信是 -40,815 股，正負號相反。
- 9/18（17:03 寫入）、9/21（次日寫入）、9/23 則一致。
- 以 store 計算 5 日投信累計為 350,309 股，HiStock 五日加總為 585 張，確實少算約 40%。

五、定義與規則
- FinMind 文件說明：盤後定價、鉅額配對等成交可能在初次公布後才補計進當日統計；買賣雙方不屬同一法人別時，淨額會改變；官方沒有固定的定案時間，建議在交易日後一至兩個營業日再取用。它排定的個股法人更新時間是 20:00，不是 16:30。
- master.tw（finding 引用的網站，另查到不同的列）：
  - 「上市投信 20:30至21:00」
  - 鉅額逐筆交易「9：00至17：00」
- T86 官方附註明寫統計含鉅額與盤後定價。16:30 的版本漏掉之後補計的成交，不符合官方的統計範圍。

需要修正的細節
1. finding 的「27/1,332」「10/6,146」是任一欄（含買、賣量）不同的列數；淨額不同的列數較少，見第三點。
2. 01:5x 的版本不能保證是「官方終版」，因為 FinMind 指出官方沒有定案時間。
3. 修法中的「移到 17:30 之後」不夠：上市投信的供應時間在 20:30～21:00，9/17 16:40 寫入的版本也仍有錯。應採用 21:40 班 force 重抓，或次一、二個交易日回頭重抓覆寫。

嚴重度維持 P1
- 這是結構性問題，每個交易日都會發生，錯誤值會永久保留，使用者沒有任何介面能修正。
- 不符合 T86 的官方統計範圍（含鉅額、盤後定價），屬於錯誤的金融口徑。
- 影響大型股與自選的 2454（觀察的 7 天中有 3 天錯，1 天正負號相反），且全市場投信加總偏差 13～28%。
- 錯誤值會流入 5 日因子、選股篩選、研究脈絡與市場彙總。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:177-192（16:30 班，refresh_institutional=True）；194-210（21:40 班沒有傳 force；註解寫「已落盤的日期會被 refresh 略過」）
      - backend/app/taiwan/daily_update.py:324-328（i_max 已等於 target 時，resolve_missing_date_range 回傳 None，記為 'Institutional store already current'）；136-137（earliest_available >= target_latest 時回傳 None）；17（docstring：'0 rewrite of existing partitions'）
      - backend/app/taiwan/institutional_margin_refresh.py:143（非 force 時 available=既有日期集合）、166-169（在 available 內就略過）
      - backend/app/taiwan/enrichment_backfill.py:244-248（回補沒有 force 參數，同樣略過已存在的日期）
      - data/backend.log:45470、45478：2026-09-23 21:40 班執行完畢，inst=success；但 date=2026-09-23\part.parquet 的 mtime 仍為 2026-09-23 16:30:09，證明沒有重寫
      - 本地重算（AUDIT_SCRATCH/work/verify_inst_refetch/diff.out、diff2.py）：任一欄不同的列數 9/22 TWSE 27、TPEX 10；9/23 TWSE 28、TPEX 7。淨額不同的列數 9/22 為 17+5、9/23 為 17+7，幾乎都是投信
      - 本地重算：2454 在 9/22 的投信買進 store 289,920、官方 446,920，賣出同為 73,749，淨額 216,171 對 373,171；合計 1,815,448 對 1,972,448。上市投信全市場加總 9/22 為 11,836,489 對 16,543,539 股，9/23 為 24,292,839 對 21,397,398 股
      - store 的 2454 投信淨額（股）：9/16 -457,422、9/17 -40,815、9/18 -119,993、9/21 312,803、9/22 216,171、9/23 -17,857；9/17～9/23 五日合計 350,309 股
      - https://histock.tw/stock/chips.aspx?no=2454（2454 的投信／合計，單位張）：2026/09/22 投信 373、總計 1,972；09/17 投信 37、總計 420；09/16 投信 -483、總計 729；09/18 投信 -120、總計 3,058；09/21 投信 313、總計 3,816；09/23 投信 -18、總計 520
      - https://finmind.github.io/tutor/TaiwanMarket/Chip/：「少數交易（例如盤後定價、鉅額配對等成交）可能於初次公布後才被補計進當日統計……官方未提供固定的「最終定案時間」……若需已定案數字，建議於該交易日後一至兩個營業日再取用。」；TaiwanStockInstitutionalInvestorsBuySell「資料更新時間 星期一至五 20:00」
      - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm：「上市投信 20:30至21:00」、「上櫃 投信及外資 17:30」；Q4 鉅額交易「逐筆交易 9：00至17：00」
      - reference/raw/twse_t86_20260922.json notes：「本統計資訊含一般、零股、盤後定價、鉅額，不含拍賣、標購。」；reference/raw/tpex_insti_daily_trade_20260922.json subtitle：「含普通股、鉅額、零股、綜合帳戶之投信買賣成交量」
      - 下游使用：backend/app/taiwan/detail_service.py:423-440（5 日累計直接從 store 加總）；market_intelligence.py:494-505（全市場法人加總讀 store）；screener.py:409（篩選讀 store 最新值）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：16:30 班只要寫入當日法人分區，之後 21:40 班、隔日排程與 UI 回補都會因為「該日已有分區」而略過；只有 CLI 的 --force 會重抓。當天寫入的分區因此永久保留初版資料，與官方後來的版本不同。以 9/22、9/23 為例：
- 淨額不同的列數：上市每天 17 檔（約 1.3%），上櫃每天 5～7 檔（約 0.1%）。若把買進、賣出欄位也算進去，上市為 27～28 檔（約 2%）。
- 被修正的主要是投信淨額，外資淨額只在 9/22 有 2 檔改變。
- 投信淨額最大差：上櫃 3264 在 9/23 為 -5,386,000 股，上市 3481 為 -3,725,000 股。
- 合計方向相反的有 6 檔，包括 8028、2383、3605。
- 初版還缺少 K 結尾的外幣 ETF 列。
- 自選股 2454：9/22 投信 216 張，官方 373 張；9/17 投信 -41 張，Yahoo +37 張，方向相反。5 日投信累計 350,309 股，Yahoo 為 585 張，少算約 40%。
- 上市投信全市場加總：9/22 為 11,836 張，官方 16,544 張。
修正時應在 21:40 以後，或次一交易日強制重抓最近 1～2 個交易日。第三方時程表列出上市投信在 20:30～21:00 才定版，所以只把抓取延到 17:30 之後不夠。
    - 理由：我自己寫腳本，拿本地 parquet 分區比對官方參考資料（T86 與 TPEx dailyTrade，9/24 01:5x 取得），也另外抓了 Yahoo 做核對。finding 的核心數值全部可以重現，我算出的結果與 finding 並列如下：
(1) 9/22 與 9/23 兩天，任一欄位不同（含買進、賣出）的列數：上市 27/1332 對 finding 27/1332；上櫃 10/6146 對 10/6146；9/23 上市 28/1329 對 28/1329；上櫃 7/5670 對 7/5670。全部相同。
(2) 2454 在 9/22：投信 store 216,171，官方 373,171；合計 store 1,815,448，官方 1,972,448。與 finding 相同。
(3) 合計方向相反：8028 為 -2,969,992 對 +37,008；2383 為 211,866 對 -272,574；3605 為 -605,007 對 1,461,993。與 finding 相同。另外找到 finding 沒列的 8996（9/22）、6831（上市，9/23）、7856（上櫃，9/23）。
(4) 上市投信全市場加總（9/22）：store 11,836.5 張，官方 16,543.5 張。與 finding 相同。
(5) 2454 的 5 日投信累計：store 取 9/17 到 9/23 五個分區，加總 350,309 股，與 finding 相同。Yahoo 五日為 37−120+313+373−18＝585 張，少算約 40%。
(6) Yahoo 對 2454 的比對：9/16 投信 −483 張，store −457,422；9/17 投信 +37 張，store −40,815，正負號相反；9/22 也不同。9/10、9/11、9/14、9/15、9/18、9/21、9/23 都相符。3034 在 9/10 到 9/23 每天都相符。
(7) 分區修改時間：9/22 為 16:30:07，9/23 為 16:30:09。8～9 月在交易日當天寫入的只有 9/16、9/17、9/18、9/22、9/23。與 finding 相同。
程式碼路徑也成立：兩個 adapter 的 URL 與參考資料的 URL 相同，所以差異不是端點或解析造成的。resolve_missing_date_range 在 i_max 大於等於 target 時回傳 None；refresh_dates 會略過已經有資料的日期；UI 回補（enrichment_backfill）沒有傳 force。只有 CLI 的 --force 會重抓。
我另外找到一個 finding 沒提的佐證：當天寫入的 5 個分區，K 結尾的外幣 ETF 都是 0 列；事後寫入的分區（9/10、9/11、9/14、9/15、9/21）則有 3～5 列。官方 9/22 有 4 列，其中 00636K 的自營商為 +500；9/23 有 3 列。這表示當天初版的資料集本身就不同，不只是數值被修正。
finding 裡有三處敘述不精確：
(a)「投信淨額最大差 3,725,000 股（3481 9/23）」只在上市成立。全市場的最大差是上櫃 3264 在 9/23 的 −5,386,000 股；3264 在 9/22 的外資淨額也差了 15,972,408 股。
(b)「約 2% 的個股」只在把買進、賣出欄位也算進去、而且只看上市時成立。只看使用者看得到的淨額：上市每天 17 檔（約 1.3%），上櫃每天 5～7 檔（約 0.1%），全市場約 0.3%。
(c) 外資淨額只在 9/22 有 2 列改變，9/23 沒有，主要被修正的是投信。外資的買進、賣出成對改變、淨額不變，符合外資之間鉅額交易的型態（推測）。
另外，master.tw（第三方的時程表，沒有標示更新日期）列出「上市投信 20:30至21:00」。所以修正建議裡「移到 17:30 之後」不夠，應該在 21:40 或次一交易日強制重抓。以 P1 計：錯誤值會永久保存，而且已經影響自選股 2454 的 5 日因子與單日方向。
    - 證據：
      - 程式碼：backend/app/jobs/daily_pipeline.py:177-208（16:30 與 21:40 兩班都是 refresh_institutional=True，也都沒有 force）
      - 程式碼：backend/app/taiwan/daily_update.py:136-137（earliest_available 大於等於 target_latest 時回傳 None）、324-328（備註為 Institutional store already current）
      - 程式碼：backend/app/taiwan/institutional_margin_refresh.py:143、166-169（available 裡的日期一律略過）；backend/app/taiwan/enrichment_backfill.py:244-248（UI 回補沒有傳 force）
      - 程式碼：backend/app/taiwan/enrichment/institutional.py:47、152（URL 與參考資料相同：T86 selectType=ALLBUT0999、TPEx dailyTrade sect=AL）
      - 程式碼：backend/app/taiwan/detail_service.py:437-446（5 日因子是 store 最近 5 個分區的加總）
      - 本地計算（uv run python ...\scratchpad\audit\work\verify-instrefetch\compare_all.py）：9/22 任一欄位不同的列數，上市 27/1332、上櫃 10/6146；9/23 上市 28/1329、上櫃 7/5670。只看淨額：兩天上市都是 17 列，上櫃分別是 5 列與 7 列
      - 本地計算（compare.py）：9/22 的 2454，s_tn=216171、o_tn=373171，s_tot=1815448、o_tot=1972448；合計方向相反的有 3605（-605007 對 1461993）、8996（-92147 對 241853）、8028（-2969992 對 37008）、2383（211866 對 -272574）；9/23 有 6831（32904 對 -57096）、7856 上櫃（-14558 對 167442）
      - 本地計算（compare.py）：投信淨額最大差，9/23 是 3264 上櫃的 -5,386,000，其次是 3481 上市的 -3,725,000；9/22 是 8028 的 3,007,000。外資淨額最大差是 9/22 的 3264，15,972,408
      - 本地計算（compare.py）：上市投信加總，9/22 store 11836.5 張、官方 16543.5 張；9/23 store 24292.8 張、官方 21397.4 張。上櫃投信加總，9/22 store 4113.1 張、官方 2578.1 張；9/23 store -4060.3 張、官方 -8653.9 張
      - 本地計算（store_2454.py）：2454 的 5 日窗口是 9/17、9/18、9/21、9/22、9/23，投信加總 350309 股；9/16 為 tn=-457422，9/17 為 tn=-40815
      - 本地計算（store_2454.py）：K 結尾的列數，當天寫入的 9/16、9/17、9/18、9/22、9/23 都是 0；事後寫入的 9/10 有 3、9/11 有 5、9/14 有 4、9/15 有 4、9/21 有 3。官方 9/22 只在官方出現的列是 00636K（自營商 +500）、00643K、00668K、00657K
      - stat mtime：date=2026-09-22 為 2026-09-22 16:30:07；date=2026-09-23 為 2026-09-23 16:30:09；9/16 為 16:30:37；9/17 為 16:40:21；9/18 為 17:03:39；9/21 為 2026-09-22 10:55:44；8/03～9/15 都是 2026-09-17 事後寫入
      - https://tw.stock.yahoo.com/quote/2454.TW/institutional-trading：9/23 投信 -18、合計 520；9/22 投信 373、合計 1,972；9/21 投信 313；9/18 投信 -120；9/17 投信 37、合計 420；9/16 投信 -483、合計 729（五日加總 585 張）
      - https://tw.stock.yahoo.com/quote/3034.TW/institutional-trading：9/10～9/23 的每一天都與 store 四捨五入後相符（例如 9/17 投信 -22、9/16 投信 343）
      - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm（第三方，沒有標示更新日期）：「上市投信」20:30至21:00；「上櫃 投信及外資」17:30；鉅額「逐筆交易 9：00至17：00」
      - T86 notes（reference/raw/twse_t86_20260922.json）：「本統計資訊含一般、零股、盤後定價、鉅額，不含拍賣、標購。」「本資訊以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計。」TPEx subtitle：「含普通股、鉅額、零股、綜合帳戶之投信買賣成交量」

### [P2 → P2] 個股詳細頁的即時抓取路徑不計算券資比：只要即時抓取成功，「券資比」與「最新券資比因子」一律是「--」（確認成立）

- **主張**：融資 adapter 固定把 short_margin_ratio 設成 None（註解寫由 factor 層計算）。_aggregate_margin 即時路徑直接轉傳這個 None，沒有任何地方補算；只有 store 備援路徑有值。
- **正確的定義或數值**：2454 在 9/23 的詳細頁券資比應顯示 0.71%。
- **app 實際行為**：即時抓取成功時顯示「--」；只有在證交所限流或逾時、改走 store 備援時才有數值。
- **對使用者的影響**：即時行情開啟時打開任何自選股詳細頁，「融資融券」卡的券資比與「核心籌碼滾動因子」的最新券資比都看不到。這是明確的功能缺失。
- **修法建議**：在 _aggregate_margin 即時路徑補上 round(short_balance / margin_balance * 100, 2)（margin_balance > 0 時計算，否則為 None），與 margin_store 用同一個 helper；並補一個即時路徑的券資比測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enrichment/margin.py:130、228（short_margin_ratio=None）
  - backend/app/taiwan/enrichment/models.py:182（註解：「Derived by the factor layer, not the raw provider」）
  - backend/app/taiwan/detail_service.py:506（short_margin_ratio=m.short_margin_ratio）、:576（因子沿用）
  - frontend/src/pages/TaiwanStockDetail.tsx:717、767（null 時顯示 '--'）
  - 對照：backend/app/taiwan/margin_store.py:130-136（store 寫入時才計算）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW/margin：2026/09/23 券資比 0.71%；頁面定義「券資比 = 融券餘額 / 融資餘額 x 100%」
- **本地計算證據**：
  - 注入官方 9/23 MI_MARGN 原始 JSON 並呼叫 _aggregate_margin：2454 券資比為 None（store 備援為 0.71）；3034 為 None（3.87）；6196 為 None（0.65）；00893 為 None（15.79）。_aggregate_factors 的 short_margin_ratio 也全是 None（app_check.out 第 14-29 行）
  - 兩交易所 adapter 解析結果中，short_margin_ratio 非 null 的列數都是 0（1,297 列與 919 列；app_check.out 第 4、6、9、11 行）
  - 測試沒有涵蓋即時路徑的券資比（tests/test_taiwan_data_enrichment.py:210、242 甚至斷言它是 None）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：成立。個股詳細頁在即時抓取成功時（這是常態，自選 9 檔都會走這條路徑），會轉傳 adapter 固定設為 None 的 short_margin_ratio，整條路徑沒有補算，所以「融資融券」卡的券資比與「核心籌碼滾動因子」的最新券資比一律顯示「--」。只有在即時路徑拋出例外、或 5 個工作日都抓不到資料而改走 store 備援時才有值。正確口徑是券資比＝融券餘額÷融資餘額×100%：2454 在 9/23 應為 0.71%（73 張 ÷ 10,300 張），HiStock 與官方原始資料的推算一致。
finding 沒提到的緩解因素：同一頁切到融資分頁後，歷史表格讀 store，仍看得到券資比。
修法建議：即時路徑的計算要沿用 store 的規則（margin_balance > 0 才計算，否則為 None，round 到 2 位）。不要用 compute_margin_factors 的 otherwise(0.0)，否則融資餘額為 0 時會顯示 0.00%。另外補一個即時路徑的測試。
    - 理由：我逐一讀了 finding 引用的程式碼，行號都沒有偏移。
1. 兩個 adapter 固定把 short_margin_ratio 設成 None（margin.py:130、228），model 的註解也寫明這個值由 factor 層計算（models.py:182）。
2. 詳細頁的端點是 /api/taiwan/stocks/{symbol}（api/taiwan.py:270-287），呼叫 get_stock_detail，再依序進入 _aggregate_margin（detail_service.py:154）與 _aggregate_factors（:158）。
3. _aggregate_margin 即時抓取成功時，把 m.short_margin_ratio 原封不動轉傳（:506）；_aggregate_factors 再沿用 margin.short_margin_ratio（:576）。detail_service.py 沒有呼叫 compute_margin_factors（factors.py:45），也沒有自行計算，所以這條路徑上券資比一定是 None。
4. 只有兩種情況會有值：即時路徑拋出例外，或 5 個工作日都抓不到資料。這時改走 _margin_from_store（:521-552），讀的是 store 寫入時算好的值（margin_store.py:129-136）。
5. 前端在值為 null 時顯示「--」（TaiwanStockDetail.tsx:717、767）。
6. 這個問題不在 docs/code-review-2026-09-22.md 的已知清單內。B9 與 N4 分別處理備援的日期與日期標記，都沒有提到券資比。

本地重現：我把官方 9/23 MI_MARGN 與 TPEx 的原始 JSON 注入替身 provider，直接呼叫 _aggregate_margin 與 _aggregate_factors，沒有啟動後端，也沒有寫入任何資料。自選 9 檔都走即時路徑（src=twse:mi_margn），live_ratio 與 factor_ratio 全是 None；同一天 store 的值與手算的 short/margin×100 一致。

定義查證：我用了與 finding 不同的來源。
- HiStock 2454 的資券頁：9/23 融資餘額 10,300 張、融券餘額 73 張、券資比 0.71%。
- 官方資料解析後 mbal=10,300,000 股、sbal=73,000 股，73/10300×100=0.71。這證實分子是融券、分母是融資，也與 repo 自己的口徑一致（margin_store.py:133、factors.py:58、docs/taiwan-market-contract-v1.md:21）。
- MoneyDJ 理財百科的原文寫「券資比是融資餘額除以融券餘額的比率」，照字面是分子分母顛倒。但同一條目又說「券資比越高，代表軋空的可能性越大」，而且依字面算會得到 141，與 HiStock、Yahoo 公布的 0.71% 對不上，所以判斷是該條目的措辭錯誤，不影響本案結論。

使用者情境：自選 9 檔都是上市股票與 ETF，都在 MI_MARGN 裡。只要證交所正常回應，打開詳細頁一定走即時路徑，「融資融券」卡的券資比與「核心籌碼滾動因子」的最新券資比都顯示「--」。

finding 沒提到的緩解因素：同一頁切到融資分頁後，歷史表格讀的是 store（TaiwanStockDetail.tsx:1146-1150、1255；margin-history 端點），那裡仍看得到券資比。所以不是完全看不到，但兩張主要卡片在常態下確實失效。docs/features.md:26 把「顯示…券資比」列為詳細頁的功能，這屬於明確的功能錯誤，不是錯誤的金融口徑。依 CONTRIBUTING 第 11 節維持 P2。
    - 證據：
      - backend/app/taiwan/enrichment/margin.py:130、:228：`short_margin_ratio=None,`（TWSE 與 TPEx adapter）
      - backend/app/taiwan/enrichment/models.py:182：`short_margin_ratio: float | None  # Derived by the factor layer, not the raw provider`
      - backend/app/taiwan/detail_service.py:491-508：即時路徑的 `short_margin_ratio=m.short_margin_ratio`（:506）；:576 因子沿用；:512 只有即時路徑失敗才走 _margin_from_store（:545 取 store 的值）
      - backend/app/api/taiwan.py:270-287：詳細頁端點呼叫 get_stock_detail；grep 確認 app 內沒有任何地方呼叫 compute_margin_factors（只有 factors.py:45 定義與 enrichment/__init__.py 匯出）
      - backend/app/taiwan/margin_store.py:129-136：store 寫入時才計算 short_balance/margin_balance*100 並 round(2)
      - frontend/src/pages/TaiwanStockDetail.tsx:717、:767：值為 null 時顯示 '--'；:1146-1150、:1255：融資分頁的歷史表格讀 margin-history（store），那裡有券資比
      - docs/features.md:26：「顯示三大法人、融資融券、券資比與核心籌碼滾動因子。」（這是文件列明的功能）
      - 本地計算：`cd backend && uv run python ...\scratchpad\audit\work\verify_smr_def\check.py`（輸出在同目錄的 check.out）。兩交易所 adapter 解析結果：twse 1297 列、tpex 919 列，券資比非 null 都是 0 列。_aggregate_margin 與 _aggregate_factors 的結果：2454 live_ratio=None factor_ratio=None，手算=store=0.71；3034 None/None，3.87；2379 None/None，1.31；6196 None/None，0.65；3545 None/None，1.17；0050 None/None，1.07；00878 None/None，1.04；00885 None/None，5.33；00893 None/None，15.79。9 檔都是 src=twse:mi_margn、td=2026-09-23
      - 線上來源：https://histock.tw/stock/chips.aspx?no=2454&m=mg，2026/09/23 融資餘額 10,300 張、融券餘額 73 張、券資比 0.71%；2026/09/22 為 10,358 張、80 張、0.77%。73/10300×100=0.71，證實券資比＝融券餘額÷融資餘額×100%
      - 線上來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=de0413d1-f0a3-45c7-9f63-b955d3e0e9ec，原文「券資比是融資餘額除以融券餘額的比率。…一般來說，券資比越高，代表軋空的可能性越大。」照字面分子分母顛倒，與該條目自己的解讀及實際公布值（0.71% 而非 141）矛盾，判斷是措辭錯誤
      - git log -S'short_margin_ratio=None' 與 -S'short_margin_ratio=m.short_margin_ratio' 都只指向 fd79b63（初次合併），代表這條路徑從導入起就沒有算券資比
      - 執行前後 git status --porcelain 相同，工作樹沒有變動
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：融資 adapter（TWSE 和 TPEx）固定把 short_margin_ratio 設成 None。詳細頁的 _aggregate_margin 即時路徑與 _aggregate_factors 原樣轉傳，沒有補算，所以只要 MI_MARGN 即時抓取成功，「融資融券」卡的券資比和「最新券資比因子」都顯示「--」。例如 2454 在 9/23 應為 0.71%，3034 應為 3.87%，00893 應為 15.79%。只有 5 個交易日都抓不到、改走 store 備援時才有數值。這條路徑不受即時行情開關控制。同頁的「融資融券歷史」彈窗讀 store，仍然顯示得出券資比。
    - 理由：重現結果與 finding 完全一致。兩個 adapter 固定把 short_margin_ratio 設成 None。_aggregate_margin 的即時路徑原樣轉傳這個 None，_aggregate_factors 也照抄，回應模型沒有 validator 會補算，前端遇到 null 就顯示「--」。只有 margin_store 在寫入時才算券資比。

我用官方 9/23 MI_MARGN 原始 JSON 做了一個假 provider，注入 _aggregate_margin（不經 __init__，並把 store 備援換成一碰就報錯的函式，確保只走即時路徑）。結果：9 檔自選股的即時路徑全部回傳 status=available、trade_date=2026-09-23、short_margin_ratio=None，因子也是 None。另外用官方融資餘額與融券餘額自己算，數值和本地 store 的 parquet、Yahoo 奇摩完全相同：2454 為 0.71，3034 為 3.87，6196 為 0.65，00893 為 15.79，和 finding 所列相同。

有兩點敘述要微調：
1. 詳細頁抓融資資料不受「即時行情開關」控制。_aggregate_margin 每次都直接打 MI_MARGN，所以只要這次抓取成功就會缺值，跟開關無關。
2. 同一頁的「融資融券歷史」彈窗（TaiwanStockDetail.tsx:1255）走 chip_history 讀 store，仍然看得到券資比。所以缺值只限於「融資融券」卡和「核心籌碼滾動因子」卡，不是整頁都看不到。

這兩點不影響核心結論，嚴重度維持 P2。
    - 證據：
      - 程式碼：backend/app/taiwan/enrichment/margin.py:130、:228 都寫 short_margin_ratio=None；margin.py:10 的模組說明卻宣稱會算券資比。
      - 程式碼：backend/app/taiwan/enrichment/models.py:182 註解為『Derived by the factor layer, not the raw provider』，但詳細頁並沒有經過任何 factor 層。
      - 程式碼：backend/app/taiwan/detail_service.py:506 為 short_margin_ratio=m.short_margin_ratio（即時路徑直接轉傳）；:545 是 store 備援；:576 的因子沿用 margin.short_margin_ratio。
      - 程式碼：backend/app/taiwan/detail_models.py:102-120 的 TaiwanMarginData／TaiwanFactorsData 沒有 validator 或 computed_field。
      - 程式碼：backend/app/taiwan/margin_store.py:129-136 只在寫入 store 時計算 round(short_balance/margin_balance*100, 2)。
      - 程式碼：frontend/src/pages/TaiwanStockDetail.tsx:717、:767 在 null 時顯示 '--'；:1255 的歷史彈窗讀 store，有值。
      - 本地重現：腳本 AUDIT_SCRATCH/work/verify-smr/repro.py，輸出 repro.out；執行指令為 cd backend && uv run python repro.py。adapter 解析 TWSE 9/23、9/22 各 1297 列，TPEx 9/23、9/22 各 919 列，short_margin_ratio 非 null 的列數都是 0。
      - 本地重現（即時路徑、store 備援已停用；每行依序為官方融資/融券餘額（張）→ 自算 → _aggregate_margin → 因子 → store parquet）：2454 為 10300/73 → 0.71 → None → None → 0.71；3034 為 2403/93 → 3.87 → None → None → 3.87；2379 為 2518/33 → 1.31 → None → None → 1.31；6196 為 1695/11 → 0.65 → None → None → 0.65；3545 為 4097/48 → 1.17 → None → None → 1.17；0050 為 64587/691 → 1.07 → None → None → 1.07；00878 為 3170/33 → 1.04 → None → None → 1.04；00885 為 2419/129 → 5.33 → None → None → 5.33；00893 為 114/18 → 15.79 → None → None → 15.79。9 檔即時路徑都是 status=available、source=twse:mi_margn、trade_date=2026-09-23。
      - 線上：https://tw.stock.yahoo.com/quote/2454.TW/margin 頁面定義『券資比 = 融券餘額 / 融資餘額 x 100%』，2026/09/23 融資餘額 10,300、融券餘額 73、券資比 0.71%（2026/09/22 為 10,358／80／0.77%），與官方原始資料及 store 一致。
      - 測試：backend/tests/test_taiwan_data_enrichment.py:210、:242 斷言 adapter 的 short_margin_ratio is None；tests/test_detail_chip_store_fallback.py:65-120 只測 store 備援與 unavailable，沒有測即時路徑的券資比。
      - 未寫入：執行前後的 git status --porcelain 相同，data\taiwan\margin\date=2026-09-23 的 ls 時間戳也相同（比對檔 git_before.txt、store_before.txt 在 verify-smr 資料夾內）。

### [P2 → P2] 市場層級法人買賣超用跨證券的股數加總，上櫃含權證、上市不含，與官方以金額計的口徑方向可能相反（確認成立）

- **主張**：market_intelligence 把當日 store 所有列的 foreign_net、investment_trust_net、dealer_net 直接加總股數。上櫃來源用 sect=AL（含 4～5 千檔權證），上市用 ALLBUT0999（不含權證）。首頁與選股頁以「張」呈現全市場外資與投信買賣超，研究脈絡也回傳這個值，但官方與財經媒體的市場法人統計以金額（元）計算。
- **正確的定義或數值**：全市場法人買賣超應採官方金額口徑（例如 BFI82U 的元，或上櫃的金額），或至少排除權證、並讓上市與上櫃的商品範圍一致。
- **app 實際行為**：是跨不同價位證券（含上櫃權證）的股數加總，數值無法與任何公開統計對照；9/23 的投信方向與官方相反。
- **對使用者的影響**：首頁「外資買賣超」與選股頁「投信買賣超」的全市場數字，可能讓使用者誤判法人整體是買超還是賣超；研究脈絡的 institutional_market_net 也是同一口徑。
- **修法建議**：市場層級改用官方金額統計（另存 BFI82U 與上櫃的法人金額）；若維持股數口徑，至少排除權證，並把標示改成「上市上櫃股數加總（非金額）」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/market_intelligence.py:503-514
  - backend/app/taiwan/enrichment/institutional.py:47（ALLBUT0999）與 :152（sect=AL）
  - frontend/src/pages/Dashboard.tsx:286-290
  - frontend/src/pages/TaiwanScreener.tsx:605-628
  - backend/app/taiwan/research_context.py:639
- **線上證據**：
  - https://www.cna.com.tw/news/afe/202609230189.aspx（9/23）：三大法人買超 389.21 億元；外資及陸資買超 373.13 億元、投信賣超 42.16 億元、自營商買超 58.24 億元
  - https://tw.stock.yahoo.com/news/三大法人買超台股608-2億元-070625507.html（9/22）：外資及陸資買超 447.26 億元、投信買超 2.4 億元
- **本地計算證據**：
  - 9/23 app 的市場投信股數加總為 +20,233 張（顯示為買超），官方為投信賣超 42.16 億元，方向相反
  - 9/22 市場外資為 +1,058,563 張，其中上櫃權證類代號 5,241 列；權證合計 -20,339 張，混入的是上市端完全沒有的權證
  - 9/23 上櫃權證合計 +11,302 張，佔 app 市場三大法人合計 409,721 張的 2.8%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：market_intelligence 把當日 store 所有列（上市 T86 ALLBUT0999 不含權證；上櫃 sect=AL 含約 4,800～5,200 檔權證與少數 ETN）的法人淨股數直接加總，首頁與選股頁以「張」顯示全市場外資與投信買賣超，研究脈絡的 institutional_market_net 也用同一個 total_net。但官方與財經網站的市場層級法人統計以金額（億元）計，而且上市、上櫃分開列。股數加總沒有依價格加權，高價股的賣超會被低價股的買超蓋過：9/23 上市投信淨股數 +21,397 張，官方金額卻是賣超 47.59 億（Yahoo／HiStock，CNA 的初值為 42.16 億）；app 的投信合計 +20,233 張，方向與官方相反；9/22 投信同樣方向相反。這個翻轉來自股數與金額的口徑差異，不是權證造成：兩天權證的投信淨額都是 0。權證混入的影響主要在自營商與 total_net（9/22 為 -20,906 張，9/23 為 +11,825 張），並造成上市與上櫃商品範圍不一致。建議市場層級改用金額口徑，上市、上櫃分開；若保留股數，至少排除權證與 ETN，並在首頁標明「股數加總（非金額）」。
    - 理由：程式碼、官方參考資料和線上來源都支持這條 finding。

程式碼部分，market_intelligence.py 直接把 inst_store 當日所有列的 foreign_net、investment_trust_net、dealer_net 加總，單位是股，沒有排除任何商品。T86 使用 selectType=ALLBUT0999，TPEx 使用 sect=AL。institutional_margin_refresh 會把上市與上櫃的全部列寫進同一個 store。首頁與選股頁把加總值除以 1000 後顯示成「張」；research_context 則把 total_net 放進 institutional_market_net。

正確口徑部分，我另外查了 Yahoo 奇摩股市的法人進出頁與 HiStock。兩者都用億元呈現市場層級的法人統計：Yahoo 的標題是「集中市場三大法人買賣金額」和「櫃買市場三大法人買賣金額」，上市與上櫃分開列。CNA 報導同樣以元計。所以市場層級應該用金額口徑。

方向相反這一點我自己重算過，確實成立。9/23 上市投信的淨股數是 +21,397,398 股。用官方 MI_INDEX 的成交金額除以成交股數當均價、乘上各股投信淨股數，估出來是 -48.26 億，和 Yahoo／HiStock 的 -47.59 億、CNA 的 -42.16 億方向一致。原因是價格加權：投信賣的是高價股（台積電 -1,142,808 股，均價約 2,493 元，約 -28.5 億），買的是低價股（長榮航 +7,481,000 股，均價約 42.8 元）。同樣算法算出的上櫃投信是 -8.70 億、上櫃外資是 -174.9 億，和 Yahoo 的上櫃 -8.17 億、-174 億相符，證明金額才是對得上官方的口徑。app 的 store 在 9/23 投信合計是 +20,232,552 股，約 +20,233 張，方向與官方相反。9/22 也一樣：app 是 +15,949,609 股，Yahoo／HiStock 在我查詢時的數字是投信 -3.66 億。finding 引用的 9/22 Yahoo 新聞寫 +2.4 億，兩者不同，推測是投信資料在初次公布後有修正。

finding 有一處需要修正。投信方向相反與權證無關：兩天的上櫃權證類列，投信淨額都是 0，方向翻轉完全來自跨價位的股數加總。權證混入主要影響自營商（9/22 為 -20,906,000 股，9/23 為 +11,825,000 股），因此影響 total_net，也就是研究脈絡的 institutional_market_net；對外資只有 +603,000 股與 -441,000 股，影響很小。另外，finding 的「權證類 5,241 列」包含 4 檔 020xxx ETN（例如統一亞洲半導體N），嚴格說不是權證。這是小瑕疵，數字本身我可以重現。

嚴重度部分，這些數值只用於顯示與研究脈絡，不進入策略或訊號；選股頁也標了「全市場總計 (股數/1000)」，所以維持 P2 合理。不過首頁卡片只寫「張」，沒有註明是股數加總；若依 CONTRIBUTING 的「錯誤金融口徑」嚴格解讀，也可以主張 P1。

使用者情境：純台股部署，首頁台股卡與選股頁都會顯示這兩個數字，所以一定會看到。

另有一個不在本 finding 範圍內的觀察，交給協調者評估，原因部分是推測：store 的 9/22、9/23 分區分別在當天 16:30:07 和 16:30:09 寫入，與隔天凌晨下載的官方參考資料相比，每天都有 26～27 檔上市股票的投信淨額不同，外資則相同。其中包括使用者自選的 2454（9/22：store 216,171 股，參考資料 373,171 股）以及 2330（9/23：-973,707 股對 -1,142,808 股）。refresh 遇到已存在的日期會跳過，不會重抓，所以投信修正後的數字永遠不會進入 store。
    - 證據：
      - backend/app/taiwan/market_intelligence.py:503-507：f_net／it_net／d_net 是 inst_df 各欄的 .sum()，沒有過濾商品，也沒有換算金額
      - backend/app/taiwan/enrichment/institutional.py:47 selectType=ALLBUT0999；:152 sect=AL
      - backend/app/taiwan/institutional_margin_refresh.py:179-189：all_flows = flows_twse + flows_tpex，全部寫進 store
      - frontend/src/pages/Dashboard.tsx:286-290：「外資買賣超」顯示為 foreign_net/1000 加「張」，沒有註明是股數加總
      - frontend/src/pages/TaiwanScreener.tsx:605-628：外資與投信卡片，註記為「全市場總計 (股數/1000)」
      - backend/app/taiwan/research_context.py:639：institutional_market_net=market_snap.institutional.total_net
      - 線上（與 finding 不同的來源）https://tw.stock.yahoo.com/institutional-trading：標題為「集中市場三大法人買賣金額」與「櫃買市場三大法人買賣金額」，單位是億（買進(億)／賣出(億)／買賣超(億)）；上市 9/23 外資 373.13、投信 -47.59、自營 58.25、合計 383.79；9/22 投信 -3.66；上櫃 9/23 外資 -174、投信 -8.17、自營 -9.05
      - 線上 https://histock.tw/stock/three.aspx：「近三十日上市三大法人買賣超」，單位億元；09/23 投信 -47.59、09/22 投信 -3.66
      - 線上 https://www.cna.com.tw/news/afe/202609230189.aspx：已確認 9/23 三大法人買超 389.21 億元、外資 373.13 億、投信賣超 42.16 億、自營商 58.24 億
      - 本地計算（uv run python ...\scratchpad\audit\work\verify_inst_mkt_def\b.py）：上市 9/23 投信淨股數 21,397,398；以 MI_INDEX 成交金額／成交股數當均價，估算金額 -48.26 億；賣出金額最大的是 2330 -1,142,808 股、均價 2493.38，約 -28.49 億；買超股數最大的是 2618 +7,481,000 股、均價 42.81
      - 本地計算：上櫃 9/23 投信估算金額 -8.70 億、外資估算金額 -174.9 億，與 Yahoo 的 -8.17 億、-174 億相符
      - 本地計算（a.py）：store 9/23 共 6,999 列，投信合計 20,232,552 股（+20,233 張），外資 123,023,127，自營 266,465,131，合計 409,720,810；9/22 共 7,478 列，外資 1,058,562,705（+1,058,563 張），投信 15,949,609
      - 本地計算：上櫃權證類（7xxxx 開頭的 6 碼代號）9/22 共 5,237 列：外資 +603,000、投信 0、自營 -20,906,000；9/23 共 4,769 列：外資 -441,000、投信 0、自營 +11,825,000。投信方向翻轉與權證無關
      - 本地計算：另有 020025、020033、020040、020041 共 4 檔（9/23 為 3 檔）是 ETN（例如統一亞洲半導體N），finding 把它們算進權證類 5,241 列；嚴格說不是權證
      - 另一個觀察（範圍外）：data\taiwan\institutional\date=2026-09-22 與 date=2026-09-23 的 part.parquet 分別在當天 16:30:07、16:30:09 寫入。與參考資料比對，投信淨額不同的上市股票有 26 檔與 27 檔，例如 2454.TWSE 9/22 為 216,171 對 373,171、2330.TWSE 9/23 為 -973,707 對 -1,142,808。institutional_margin_refresh.py:166-169 遇到已存在的日期就跳過，不會重抓（推測：官方在初次公布後修正了投信資料）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：market_intelligence.py:503-514 把當日 store 裡全部證券的外資、投信、自營商淨買賣股數直接加總，當成全市場法人買賣超，首頁與選股頁用「張」顯示。上市來源 T86 ALLBUT0999 不含權證；上櫃來源 sect=AL 含約 4,800～5,200 檔權證，所以兩邊的商品範圍不一致。這種跨價位的股數加總無法和官方以金額計的統計對照。9/22 與 9/23 兩天，app 的全市場投信都顯示買超（+15,950 張、+20,233 張）；官方上市加上櫃的投信金額卻都是賣超（約 -14～-20 億、-50～-56 億）。方向相反的主因是用股數而不是金額加總：只看上市、不含權證時，投信股數仍是買超；換算成金額後，則與中央社的 -42.16 億相符。權證對外資與投信幾乎沒有影響，主要影響自營商與三大法人合計（佔 1～3%）。因此只排除權證修不好方向相反的問題，要改成金額口徑（例如官方的 BFI82U 金額統計，或用成交均價把逐檔股數換算成金額），或是明確標示這是股數加總。research_context 雖然回傳 institutional_market_net，但 AI 研究的 payload 沒有使用它。
    - 理由：我自己寫腳本重算，finding 引用的每個數字都算得出來，投信方向相反也確實存在，而且除了 9/23，9/22 也一樣。不過有三點要修正：(1) 投信方向相反的主因是「用股數加總不同價位的證券」，不是權證。兩天權證的投信淨額都是 0；只看上市、不含權證時，9/23 投信也是 +24,293 張，官方金額卻是賣超。(2) 權證對外資幾乎沒有影響：9/22 +603 張、9/23 -441 張，佔比不到 0.1%。finding 寫的 -20,339 張是權證的三大法人合計，主要來自自營商（-20,906 張）。所以 fix_suggestion 的替代做法「至少排除權證」修不好投信方向相反的問題。(3) CNA 的 -42.16 億只算上市，app 的數字是上市加上櫃。我把上櫃金額也加進去比，方向仍然相反，結論不變。
另外，我用當日均價（成交金額除以成交股數）把 store 裡每檔股數換算成金額，上市投信是 -42.84 億、外資是 +368.38 億，與中央社的 -42.16 億、+373.13 億幾乎一樣。這表示逐檔資料沒有錯，問題只出在市場層級改用股數加總。
嚴重度我維持 P2，理由如下：只影響首頁與選股頁的市場摘要卡；選股頁已標示「股數/1000」；兩天的外資與三大法人合計方向都和官方一致；沒有任何訊號或策略使用這個值；research_context 的 institutional_market_net 雖然有回傳，但 ai_research.py 組 payload 時沒有用到它。若照 CONTRIBUTING 第 11 節「錯誤金融口徑」的字面解讀，升到 P1 也說得通。
範圍外的額外觀察：store 的分區在交易日當天 16:30 寫入，隔天 01:50 下載的官方資料在 22～24 檔（主要是投信欄）與 store 不同。中央社和 HiStock 的投信金額也對不上。推測是官方盤後補登了資料，而 app 沒有重抓。這一點不影響本 finding 的結論。
    - 證據：
      - 程式碼：backend/app/taiwan/market_intelligence.py:495、503-514，用 read_range(None, target, target) 讀當日全部列，直接對 foreign_net／investment_trust_net／dealer_net 做 .sum()（單位是股），不做價格加權，也不篩選商品類別
      - 程式碼：backend/app/taiwan/enrichment/institutional.py:47 用 selectType=ALLBUT0999，:152 用 sect=AL。本地資料證實：上市 9/22 共 1,332 列、9/23 共 1,329 列，都沒有權證代號；上櫃 7 開頭的六碼權證 9/22 有 5,237 列、9/23 有 4,769 列
      - 前端：frontend/src/pages/Dashboard.tsx:286-290 顯示「外資買賣超 … 張」，沒有其他說明；frontend/src/pages/TaiwanScreener.tsx:605-628 顯示外資與投信「… 張」，副標是「全市場總計 (股數/1000)」
      - backend/app/taiwan/research_context.py:639 設定 institutional_market_net=total_net；但 backend/app/taiwan/ai_research.py:146-150 的 market_context payload 只放漲跌家數與成交額，沒有這個值
      - 本地重算（AUDIT_SCRATCH/work/verify-instmkt/recompute.py）。與 finding 並列：9/22 市場外資，本機 +1,058,563 張，finding +1,058,563 張，吻合；9/23 市場投信，本機 +20,233 張，finding +20,233 張，吻合；9/23 三大法人合計，本機 409,721 張，finding 409,721 張，吻合
      - 權證並列：9/22 上櫃六碼代號（7 開頭權證 5,237 列，加上 4 列 02 開頭）共 5,241 列，三大法人合計 -20,339 張，與 finding 吻合；其中外資只有 +603 張，投信 0，自營商 -20,906 張。9/23 上櫃六碼代號合計 +11,302 張，佔 409,721 張的 2.76%，與 finding 的 2.8% 吻合；權證的外資 -441 張、投信 0
      - 投信方向不是權證造成的：9/23 只看上市、不含權證，本機投信 +24,293 張；官方參考資料 T86 為 +21,397 張；9/22 只看上市為 +11,836 張
      - 價格加權重算（value_weight.py，9/23 均價取自參考資料的 MI_INDEX 與 TPEx dailyQuotes）：本地 store 上市投信 -42.84 億、外資 +368.38 億、自營商 +58.06 億；參考資料 T86 上市投信 -48.26 億；上櫃外資 -174.90 億、投信 -8.70 億、自營商 -9.03 億；上市加上櫃的投信 -56.10 億，但 app 顯示 +20,233 張
      - 線上 https://www.cna.com.tw/news/afe/202609230189.aspx（9/23，集中市場）原文數值：三大法人合計買超 389.21 億元、外資及陸資買超 373.13 億元、投信賣超 42.16 億元、自營商買超 58.24 億元
      - 線上 https://tw.stock.yahoo.com/news/三大法人買超台股608-2億元-070625507.html（9/22）原文數值：外資及陸資買超 447.26 億元、投信買超 2.4 億元、自營商買超 158.54 億元
      - 線上 https://histock.tw/stock/three.aspx 上市：9/23 外資 373.13 億、投信 -47.59 億、自營 58.25 億；9/22 外資 453.21 億、投信 -3.66 億；https://histock.tw/stock/three.aspx?no=TWOI 上櫃：9/23 外資 -182.11 億、投信 -8.11 億、自營 -9.21 億；9/22 外資 128.49 億、投信 -16.14 億、自營 2.39 億
      - 上市加上櫃的金額合計：9/23 投信約 -50～-56 億，app 為 +20,233 張，方向相反；9/22 投信約 -13.7～-19.8 億，app 為 +15,950 張，方向也相反（這一天 finding 沒提）。兩天的外資與三大法人合計，app 的方向都與官方金額一致
      - 範圍外：AUDIT_SCRATCH/work/verify-instmkt/diff_ref.py 的結果。9/22 有 22 檔數值不一致、9/23 有 24 檔；參考資料另多出 4 檔／3 檔 00xxxK 列（本地沒有）。store 的 part.parquet 修改時間是當日 16:30，參考資料在 9/24 01:50 下載。原因推測為官方盤後補登

### [P3 → P3] factors.py 的 compute_chip_factors 與 compute_margin_factors 是正式程式未使用、且語意有誤的程式（確認成立）

- **主張**：這兩個函式只被測試呼叫。rolling_sum(window_size=5, min_samples=1) 以列數計，前 4 列會把 1～4 日的加總標成 5 日；compute_margin_factors 在融資餘額為 0 時回傳 0.0，但 store 在同樣情況是 null。
- **正確的定義或數值**：5 日因子應為 5 個交易日的累計，不足 5 日時為 null；融資為 0 時券資比應為 null。
- **app 實際行為**：部分視窗仍輸出 5 日值；融資為 0 時券資比為 0。
- **對使用者的影響**：目前沒有畫面使用，但將來若有人接上這兩個函式，會產生錯誤因子。
- **修法建議**：刪除這兩個函式，或改成以交易日為視窗、不足 5 日回 null，並與 _institutional_5d 和 margin_store 共用同一套語意。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enrichment/factors.py:30、34、38（min_samples=1）
  - backend/app/taiwan/enrichment/factors.py:57-60（otherwise(0.0)）
  - grep 結果：正式程式沒有呼叫端，只有 tests/test_taiwan_data_enrichment.py:723、753
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 把 2454 從 9/14 起 8 列餵給 compute_chip_factors：9/14 的 foreign_net_5d=734,793，等於當日單日值
  - compute_margin_factors：margin_balance=0、short_balance=5000 時 short_margin_ratio 為 0.0
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：compute_chip_factors 與 compute_margin_factors 只在 enrichment/__init__.py 被匯出，正式程式沒有呼叫端，只有測試呼叫。compute_chip_factors 用 rolling_sum(window_size=5, min_samples=1) 以列數為視窗，有兩種錯誤：(1) 每檔輸入序列的前 4 列會把 1～4 日的加總當成 5 日值，例如 2454 從 9/14 起餵入時，9/14 算出 734,793，正確值是 1,223,096；(2) T86 只列有交易的證券，序列中間缺日時，5 列會跨超過 5 個交易日，例如 1516.TWSE 的 8/21 算出 -8,001，正確值是 4,999。compute_margin_factors 在 margin_balance 為 0 或 null 時，券資比回傳 0.0；margin_store 的正本在同樣情況存 null。實際資料中沒有融資為 0、融券大於 0 的情況，差異出現在 19,278 列融資與融券都是 0 的資料上。目前沒有畫面使用這兩個函式，屬於維護風險。
    - 理由：finding 的三個說法我都用官方 T86、本地 parquet 和自己寫的腳本重現了，數值一致。
(1) 沒有正式呼叫端：compute_chip_factors 與 compute_margin_factors 只在 enrichment/__init__.py:13、56-57 被重新匯出。app/taiwan/__init__.py:11-23 沒有匯入這兩個函式。其他呼叫只有 tests/test_taiwan_data_enrichment.py:723、753。
(2) 前幾列的 5 日值其實不足 5 日：把 2454.TWSE 自 9/14 起的 8 列餵給 compute_chip_factors，9/14 的 foreign_net_5d 是 734,793，正好等於當天單日值，和 finding 一致。依 store 的完整交易日重算，正確的 5 日值是 1,223,096（9/08～9/14）。前 4 列（9/14～9/17）全部不符；第 5 列（9/18）起才相符，9/23 兩邊都是 8,381,759。9/22、9/23 的單日 foreign_net 與官方 T86 相符（1,541,804、496,417），代表計算的基礎資料沒有錯。
(3) 融資餘額為 0 時的差異：compute_margin_factors 在 margin_balance=0、short_balance=5000 時回傳 0.0。同樣的輸入套用 margin_store.py:132-135 的算式，結果是 null，和 finding 一致。
我另外找到兩個 finding 沒提到的面向，但都不改變結論。
(a) 以列數為視窗的問題不只出現在序列開頭。T86 只列出當日有交易的證券，所以序列中間缺日時，5 列會跨超過 5 個交易日。本地有 346 檔 4 碼證券在序列中間缺日。以 1516.TWSE 為例，8/19～8/24 共 4 天算錯，其中 8/21 函式算出 -8,001，正確值是 4,999。自選 9 檔都是 119/119 列、中間沒有缺日。
(b) margin_balance 為 null 時，函式也回傳 0.0。
finding 舉的「融資為 0、融券 5000」是人造輸入。實際資料中，store 有 19,278 列 margin_balance=0，它們的 short_balance 全部是 0。官方 TWSE 與 TPEx 在 9/22～9/23 也沒有融資為 0、融券大於 0 的資料。所以實際會出現的差異是：這 19,278 列 store 存 null，函式卻回傳 0.0。
這兩個函式目前沒有正式呼叫端，對使用者沒有影響，維持 P3。
    - 證據：
      - backend/app/taiwan/enrichment/factors.py:30、34、38：rolling_sum(window_size=5, min_samples=1).over("symbol")；factors.py:57-59：pl.when(margin_balance > 0)...otherwise(0.0)
      - backend/app/taiwan/margin_store.py:132-135：margin_balance 為 null 或 ≤0 時，short_margin_ratio 設為 null
      - backend/app/taiwan/detail_service.py:425-446：_institutional_5d 取 store 最近 5 個交易日，視窗不足或缺 as_of 時回傳 None
      - Grep compute_chip_factors|compute_margin_factors：只出現在 enrichment/__init__.py:9,13,56,57（匯出）、factors.py 定義處、tests/test_taiwan_data_enrichment.py:23,723,753；app/taiwan/__init__.py:11-23 沒有匯入這兩個函式
      - 腳本：AUDIT_SCRATCH/work/verify-chipmarginfactors/verify.py；輸出：out.txt（以 cd backend && uv run python verify.py 執行，exit=0）
      - 2454.TWSE 自 9/14 起 8 列的比對（函式值／正確 5 交易日值）：9/14 734,793／1,223,096；9/15 -265,206／-47,834；9/16 731,822／1,706,793；9/17 1,032,298／290,258；9/18 3,918,493／3,918,493；9/21 6,340,567／6,340,567；9/22 8,882,370／8,882,370；9/23 8,381,759／8,381,759。finding 的 9/14=734,793 完全重現
      - 官方 T86 參考資料 csv/twse_t86_20260922_20260923.csv：2454 的外資買賣超（不含外資自營商＋外資自營商）9/22 為 1,541,804、9/23 為 496,417，與 store 的 foreign_net 相同
      - compute_margin_factors 的測試輸入 margin_balance=0、short_balance=5000，輸出 short_margin_ratio=0.0；同樣輸入套用 margin_store 算式，輸出為 null。margin_balance=null 時，函式同樣回傳 0.0（finding 未提）
      - verify2.py／out2.txt：store 有 19,278 列 margin_balance==0，short_balance>0 的有 0 列，store 的 short_margin_ratio 全為 null；餵入函式後 19,278 列全部回傳 0.0
      - 官方 MI_MARGN 與 TPEx margin/balance 在 9/22～9/23 沒有融資餘額為 0、融券餘額大於 0 的資料（兩邊都是 0 筆）
      - 序列中間缺日（finding 未提）：346 檔 4 碼證券在 store 中間缺日。1516.TWSE 在 8/19 函式值 -14,000、正確值 -9,000；8/21 函式值 -8,001、正確值 4,999；8/24 函式值 999、正確值 -3,001（函式的視窗從 8/14 起算，正確視窗從 8/17 起算）
      - 自選 9 檔在 store 都是 119 列、從 2026-04-07 到 2026-09-23，中間沒有缺日
      - 本 finding 範圍外、需另案查證：data/taiwan/institutional/date=2026-09-22 的 2454.TWSE，investment_trust_buy=289,920、investment_trust_net=216,171；官方 T86 raw/twse_t86_20260922.json 為 446,920／373,171（差 157,000）。foreign_buy/sell 也各差 68,000，但 net 相同
      - 線上查詢：WebSearch 額度已用完（200/200），未另查網頁定義；官方數據一律以已下載的參考資料比對

### [P3 → P3] 「核心籌碼滾動因子」卡標示「近 5 日指標」，卻包含 1 日融資變動量與當日券資比（確認成立）

- **主張**：卡片標題寫近 5 日，但「融資變動量」取的是 margin.margin_change（單日增減），「最新券資比因子」是當日值。
- **正確的定義或數值**：標示應區分哪些是 5 日累計、哪些是單日值。
- **app 實際行為**：單日值放在「近 5 日指標」標題下。
- **對使用者的影響**：使用者可能把單日融資增減誤讀成 5 日累計。
- **修法建議**：把標籤改為「融資增減（當日）」，或提供真正的 5 日融資增減（research_context 已有同樣算法）。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/pages/TaiwanStockDetail.tsx:730、752-760、765-767
  - backend/app/taiwan/detail_service.py:575
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 的 2454：因子卡的融資變動量為 -58,000 股，等於同日融資卡的「融資增減」，不是 5 日值
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：「核心籌碼滾動因子」卡的副標寫「近 5 日指標」，同一列另一格也標示「自營商 5 日累計」，但「融資變動量」取的是 margin.margin_change，也就是單日融資餘額增減（當日餘額減前日餘額），和融資卡的「融資增減」完全相同，標籤卻沒有寫出期間。以 9/23 的 2454 為例，卡上顯示 -58,000 股（和官方 MI_MARGN 相同），真正的近 5 日融資變動則是 -25,000 股。自選股 0050（-7,648,000 對 +46,662,000）和 00885（-76,000 對 +5,000）的單日值和 5 日值連正負號都相反。「最新券資比因子」同樣是當日值，但標籤已經寫明「最新」，所以誤導主要來自「融資變動量」。
    - 理由：我重跑了數據，結果與 finding 一致。2454 在 9/23 的因子卡「融資變動量」是 -58,000 股。這個值等於官方 MI_MARGN 當日融資餘額減前日餘額（10,300 − 10,358 張），和同頁融資卡的「融資增減」是同一個欄位。它不是近 5 日的變動：本地 margin parquet 在 9/17～9/23 共 5 個交易日，當日增減加總是 -25,000 股，和「9/23 餘額 − 9/17 前日餘額」的算法結果相同。

程式碼也證實這一點：因子卡的欄位直接取單日的 margin.margin_change，而 margin_change 在解析時定義為當日餘額減前日餘額。卡片標題寫「近 5 日指標」，同一列另一格標示「自營商 5 日累計」，「融資變動量」卻沒有標示期間，容易被讀成 5 日值。

使用者的 9 檔自選股中，每一檔的單日值都和 5 日值不同。其中 0050（-7,648,000 對 +46,662,000）和 00885（-76,000 對 +5,000）連正負號都相反，誤讀的後果比 finding 描述的更明顯。

只有一點需要修正：「最新券資比因子」確實是當日值（9/23 本地 0.71%，官方 73/10,300 張為 0.7087%，兩者一致），但它的標籤已經寫明「最新」，所以誤導主要來自「融資變動量」。這點不影響 finding 成立，嚴重度 P3 合理（屬於標示與口徑的局部體驗問題，不影響資料或交易計算）。

我只在 scratchpad 寫入腳本，沒有修改 repo 或 data。
    - 證據：
      - frontend/src/pages/TaiwanStockDetail.tsx:730：卡片副標 `近 5 日指標`；:752 標籤 `融資變動量`（同一列另一格 :751 是 `自營商 5 日累計`）；:758-759 顯示 data.factors.margin_balance_change；:765 標籤 `最新券資比因子`
      - frontend/src/pages/TaiwanStockDetail.tsx:697-700：融資卡「融資增減」顯示 data.margin.margin_change，和因子卡是同一來源
      - backend/app/taiwan/detail_service.py:575：`margin_balance_change=margin.margin_change`；:576：`short_margin_ratio=margin.short_margin_ratio`；即時路徑的來源在 :503，store 備援路徑取最後一列，在 :533、:542
      - backend/app/taiwan/enrichment/margin.py:81：`mchange = mbal - mprev`（TWSE 單日融資增減）；TPEx 版在 :186
      - 官方參考 csv/twse_mi_margn_20260922_20260923.csv：`2026-09-23,2454,聯發科,...,margin_prev_balance_lots=10358,margin_balance_lots=10300,...,short_balance_lots=73` → 單日 -58 張 = -58,000 股；券資比 73/10300 = 0.7087%
      - 本地重算（指令：cd backend && uv run python scratchpad\audit\work\verify-factorcard\repro.py）2454 本地 margin parquet 9/17～9/23 的 margin_change：-36,000、+244,000、+88,000、-263,000、-58,000 → 單日 -58,000（和官方相同）；5 日加總 -25,000；9/23 餘額 10,300,000 − 9/17 前日餘額 10,325,000 = -25,000
      - 並列比較（finding 的數字對上我的重算）：因子卡融資變動量 -58,000 對上 本地單日 -58,000 ＝ 官方單日 -58,000；真正 5 日值 -25,000
      - 自選股的單日值對上 5 日值（本地 parquet，單日值都和官方 MI_MARGN 相同）：3034 -22,000/-37,000；2379 -50,000/-118,000；6196 +136,000/+161,000；3545 -9,000/-246,000；0050 -7,648,000/+46,662,000（正負號相反）；00878 -298,000/-206,000；00885 -76,000/+5,000（正負號相反）；00893 -18,000/-4,000
      - 券資比：2454 本地 short_margin_ratio 0.71，對上官方 0.7087%，是當日值；但標籤 `最新券資比因子` 已經寫明是最新值
      - git status：TaiwanStockDetail.tsx 與 detail_service.py 都沒有未提交的修改，所以驗證的就是 HEAD 內容

### [P3 → P3] 即時抓取時，若當日 T86 沒有該股，會退回顯示前一交易日的法人資料；歷史視窗把這種「0 的日子」說成「尚未回補」（部分成立）

- **主張**：_aggregate_institutional 用 target_code 過濾後若結果為空，就往前一天再抓一次全市場 T86，把前一日的數字當作最新值顯示。但 store 與 5 日計算把缺列視為 0，兩者語意不一致。歷史視窗則把缺列日期說明為「尚未回補」。
- **正確的定義或數值**：當日 T86 沒有該股時，應視為當日法人買賣超為 0，並標示當日日期。
- **app 實際行為**：卡片顯示前一交易日的非零值，而且多下載一次全市場 T86；歷史表把該日說明成尚未回補。
- **對使用者的影響**：只影響法人交易稀疏的個股；目前自選股不受影響。
- **修法建議**：即時路徑在 T86 有資料但沒有該股時，回傳當日全 0（與 store 語意一致），不再往前一天抓；歷史視窗把「store 有該日分區但沒有該股」的日期顯示為 0。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/detail_service.py:330-345
  - backend/app/taiwan/detail_service.py:428-430（store 路徑：缺列當 0）
  - frontend/src/pages/TaiwanStockDetail.tsx:1191
- **線上證據**：
  - T86 只列出當日有法人交易的證券（reference/raw/twse_t86_20260923.json 共 1,332 列，少於上市掛牌數）
- **本地計算證據**：
  - 1410 出現在 9/22 的 T86、不在 9/23 的 T86：把 now mock 成 9/23 22:00 呼叫 _aggregate_institutional，共抓了 9/23、9/22 兩次，卡片顯示 trade_date 2026-09-22、外資 -2,000 股、5 日值以 9/22 為終點
  - 自選 9 檔在最近 60 個 store 日期都沒有缺列，目前不受影響
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：T86 只列出當日有法人交易的證券，parse_payload 用 target_code 過濾後回傳 []，程式分不出「T86 沒有該股（等於 0）」和「當天沒有 T86」。以 1410 為例：9/23 晚間抓取時會多下載一次全市場 T86，卡片退回顯示 9/22 的外資 -2,000 股，日期標為 2026-09-22；Yahoo 奇摩股市顯示 9/23 應為 0。store 備援路徑 _institutional_from_store 同樣取最後一筆存在的列，一樣顯示 9/22，並不是把缺列當 0。_institutional_5d 只有在視窗中間的缺列才等於當 0，終點日缺列時回傳 None（detail_service.py:441），與 428-430 行的 docstring 不一致。歷史視窗只列出 store 有該股列的日期，並把缺列說明成「尚未回補」；最近 60 個 store 日期中有 140 檔上市股票受影響。自選 9 檔目前不受影響。修法要三處一起改：即時路徑在 T86 有資料、但沒有該股時，回傳當日全 0；_institutional_from_store 與 _institutional_5d 的終點判斷改成「分區存在即以 0 計」；歷史視窗把「分區存在但缺列」的日期顯示為 0。只改即時路徑的話，5 日值會變成未知。
    - 理由：主體現象完全重現：我在同樣條件下的輸出，與 finding 的數字逐項相同。把 now mock 成 2026-09-23 22:00，並用官方 raw T86 取代網路，呼叫 _aggregate_institutional('1410.TWSE')，結果抓了 2 次全市場 T86（9/23、9/22）。卡片顯示 trade_date=2026-09-22，外資 -2,000、投信 0、自營 0、合計 -2,000，5 日值（外資 9,000、自營 1,000）以 9/22 為終點。對照組 2454 只抓 1 次，日期是 9/23。now 設成 9/24 10:00 時會抓 3 次（9/24、9/23、9/22），結果一樣落在 9/22。Yahoo 奇摩股市顯示 1410 在 9/23 三大法人全為 0 張、9/22 外資 -2 張，所以「當日應為 0」的預期是對的。前端 TaiwanStockDetail.tsx:1191 的說明文字確實把日期不連續解讀成「尚未回補」。

finding 對 store 端語意的描述有兩處錯，修法因此不完整：
(1) store 備援路徑 _institutional_from_store（detail_service.py:381-422）是取該股最後一筆存在的列（第 398 行），不會把缺列當 0。直接呼叫它，同樣回傳 9/22、外資 -2,000。所以不論走即時或 store 路徑，卡片都會退回前一日。問題不是「即時路徑與 store 不一致」，而是兩條路徑都沒把缺列當 0。
(2) _institutional_5d 只有在視窗中間的缺列才等於當 0。第 441 行要求終點 as_of 必須有該股的列，否則回傳 unknown：實測 _institutional_5d('1410.TWSE', 9/23) 回傳三個 None。第 428-430 行的 docstring 與實作不一致。照 finding 的修法只讓即時路徑回傳 9/23 全 0，5 日值會變成「未知」；要一併改第 441 行與 _institutional_from_store，語意才會一致。

影響範圍也重算過。官方 MI_INDEX 9/23 有 1,380 檔，其中 48 檔不在 T86，41 檔當天有成交量，16 檔前一日在 T86 有列（都會觸發退回前一日）。T86 兩天都沒有任何全 0 列，支持「不列出＝0」。store 最近 60 個日期中，有 140 檔上市股票的日期範圍內出現缺列（歷史表會把這些日子說成尚未回補），52 檔最新一列早於 9/23。自選 9 檔在最近 60 個 store 日期沒有缺列，9/22、9/23 也都在 T86 內，確實不受影響。P3 維持不變。
    - 證據：
      - 程式碼：backend/app/taiwan/detail_service.py:330-345 迴圈在 flows 為空時繼續往前一天抓。backend/app/taiwan/enrichment/institutional.py:71-72 在 target_code 不符時 continue，所以「T86 有資料但沒有該股」與「當天沒有 T86」都回傳 []，程式無法區分。
      - 程式碼：backend/app/taiwan/detail_service.py:394-398 的 _institutional_from_store 取該股最後一筆存在的列，不把缺列當 0；同檔 441 行 `if df.is_empty() or as_of not in df.get_column("date").to_list(): return unknown`，終點日缺列時 5 日值回傳 None，與 428-430 行 docstring 所說「缺列算 0」不一致。
      - 程式碼：frontend/src/pages/TaiwanStockDetail.tsx:1191 原文為「僅顯示已回補的交易日資料，日期若不連續代表該區間尚未回補，非資料異常。」；資料來源 backend/app/taiwan/chip_history.py:103-119 只輸出 store 中存在的列，不補 0。
      - 本地重現（腳本 AUDIT_SCRATCH/work/verify-inst-fallback/repro.py，輸出存於同資料夾 repro_out.txt，指令為 `uv run python repro.py`）：[1410 now=2026-09-23T22:00] fetch calls=[('TWSE','2026-09-23','1410'),('TWSE','2026-09-22','1410')]，trade_date=2026-09-22，foreign=-2000，trust=0，dealer=0，total=-2000，5d foreign=9000 trust=0 dealer=1000。與 finding 並列比較：抓取次數 2 vs 2，日期 9/22 vs 9/22，外資 -2,000 vs -2,000，全部一致。
      - 本地重現對照組：[2454 now=9/23 22:00] 只抓 1 次，trade_date=2026-09-23，foreign=496417，total=519792。[1410 now=2026-09-24T10:00] 抓 3 次（9/24、9/23、9/22），仍然顯示 9/22、-2000。
      - 本地重現：_institutional_from_store(1410) 回傳 trade_date=2026-09-22、foreign=-2000、f5=9000、d5=1000（store 路徑同樣退回前一日，不是 0）；_institutional_5d('1410.TWSE', 2026-09-23) 回傳 {foreign_net: None, investment_trust_net: None, dealer_net: None}。手算 9/16～9/22 五日：外資 13000-2000+2000-2000-2000=9000，自營 1000，與程式輸出相同。
      - 官方參考資料：raw/twse_t86_20260922.json 共 1,336 列，1410 那列為 ['1410','南染','18,000','20,000','-2,000',...,'-2,000']；raw/twse_t86_20260923.json 共 1,332 列，沒有 1410。csv/twse_mi_index_20260923_quotes.csv 顯示 1410 在 9/23 成交 1,153 股、3 筆、收 24.3。MI_INDEX 共 1,380 檔，48 檔不在 T86（其中 41 檔有成交量、16 檔 9/22 在 T86 有列）。兩天 T86 都沒有任何一列是全部買賣欄位為 0（extra_out.txt）。
      - 線上來源：https://tw.stock.yahoo.com/quote/1410.TW/institutional-trading 經 WebFetch 讀取，2026/09/23 外資 0 張、投信 0 張、自營商 0 張、合計 0 張；2026/09/22 外資 -2 張、合計 -2 張。證實 9/23 的正確值是 0，9/22 的 -2 張等於 -2,000 股。
      - store 覆蓋率（extra.py）：store 最近 60 個日期（2026-07-01～2026-09-23）有 1,381 檔上市代號，其中 140 檔在自身日期範圍內有缺列，例如 00643K 範圍內 54 個日期只有 31 列；另有 52 檔最新一列早於 9/23。1410 只缺 9/23。自選 9 檔在這 60 個日期的缺列數全部為 0，而且都在 9/22 與 9/23 的 T86 裡。
      - 唯讀確認：執行前後 git status --porcelain 相同，data\ 底下沒有任何檔案比基準時間新（find -newer 沒有輸出）。附帶觀察（不屬於本 finding）：store 的 9/22 與 9/23 分區缺少 T86 的 00636K、00657K、00668K（9/22 另缺 00643K）。

### [P3 → P3] 選股器用每檔自己的最新法人與融資列，把最多 5 週前的舊值當成當前值篩選；5 日欄位永遠是 null（部分成立）

- **主張**：_join_institutional_margin 用 read_latest_per_symbol，對當日 T86 沒有列的個股取它自己最後一筆（可能是數週前）；foreign_net_5d 與 investment_trust_net_5d 只是 null 佔位欄。
- **正確的定義或數值**：依最新交易日篩選時，沒有當日列的個股應視為 0 或標示日期。
- **app 實際行為**：沿用舊日期的買賣超參與篩選與排序。
- **對使用者的影響**：只影響選股器法人與融資條件的結果；自選股與詳細頁不受影響。
- **修法建議**：只讀最新日期的分區，缺列補 0；或在結果列顯示並依 institutional_date 過濾；移除或真正計算 5 日佔位欄。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:409-421、435-437
  - backend/app/taiwan/institutional_store.py:211-223
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 最近 40 個 store 日期出現過的 1,973 檔四碼個股中，有 118 檔的最新列早於 9/23，其中 92 檔的 foreign_net 不是 0；最舊的是 2026-08-19（例如 2867.TWSE 為 2,989,250 股）
  - TaiwanScreener.tsx 沒有顯示每列的 institutional_date
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：選股器的 _join_institutional_margin（screener.py:409-421）經 read_latest_per_symbol 讀取全部 119 個法人分區（2026-04-07 起），每檔取它自己最新的一列，但畫面只顯示全域日期「法人：2026-09-23」（TaiwanScreener.tsx:424）。實測選股器母體 2,346 列中，有 140 列的法人日期早於 9/23，其中 foreign_net≠0 的 103 列、任一淨額≠0 的 132 列。最舊到 2026-04-14（4192.TPEX，-2 股），約 5 個月，不是 5 週。

依官方 T86／TPEx 資料，這 140 列中有 137 列當日沒有法人交易，正確值應為 0。例：911868.TWSE 在 9/23 成交 210,750 股，卻顯示 9/11 的 -24,000 股。

影響量級小：
- 外資門檻 ≥1 張的 1,127 檔中有 42 檔是舊值，≤-1 張的 1,023 檔中有 53 檔。
- ≥10 張時，買賣方各只有 3 檔。
- ≥100 張時只剩 2601（9/22 的值，當天停在 9/22）。

finding 引用的 2867.TWSE 不在 security master，選股器不會出現；它的 1,973／118／92 是另一個母體的統計。融資部分不成立：只有 1 列落後，且沒有早於它自己的日線日期。

foreign_net_5d／investment_trust_net_5d 確實永遠是 null（screener.py:436-437），前端沒有顯示；只能透過 API 的 sort_by 選到，選了會退化成依代號排序。

另案（不在本 finding 範圍）：
- 日線 9/23 分區沒有任何 TPEX 列，上櫃列停在 9/22。
- store 缺 00636K／00657K／00668K 在 9/22 與 9/23 的 T86 列。
    - 理由：重現結果：機制成立，finding 自己的數字用它的方法也能一字不差算出來。但它量的母體不是選股器實際用的 universe，範例股不在選股器內，「最多 5 週」的上限也不對。融資那一半，以及舊值的量級，都沒有重現出 finding 暗示的程度。

1. 機制已確認。_join_institutional_margin 呼叫 read_latest_per_symbol，這個函式讀全部 119 個分區（2026-04-07～09-23），每檔取自己最大日期的那一列。institutional_as_of 取所有列的最大日期（2026-09-23），前端只顯示這個全域日期，沒有顯示每列的 institutional_date。

2. finding 的數字可以重現。取最近 40 個 store 日期、四碼個股：1,973 檔；最新列早於 9/23 的 118 檔；其中 foreign_net≠0 的 92 檔；最舊 2026-08-19；2867.TWSE 為 2,989,250 股。四項全部相同。

3. 母體和範例不對。security_master.parquet 完全沒有 2867，所以它不會出現在選股器。選股器實際母體是 universe（active、supported、stock/etf）與日線做 inner join，共 2,346 列。其中 140 列的法人日期早於畫面顯示的 9/23；103 列 foreign_net≠0；132 列三大法人任一淨額≠0。
   - 選股器讀全部分區，不是 40 日視窗，所以最舊到 2026-04-13／04-14（4804.TPEX、4192.TPEX），約 5 個月，不是 5 週。
   - 若只算「法人日期早於該列自己的日線日期」：96 列，其中 foreign_net≠0 的 69 列。9/23 日線分區只有 TWSE 1,330 列、沒有 TPEX，所以上櫃列的日線停在 9/22。

4. 官方比對支持「缺列應視為 0」。
   - 140 列中有 137 列不在官方 9/23 的 T86／TPEx 法人資料裡。
   - 69 列落後的上櫃列，0 列出現在官方 TPEx 9/22 資料。
   - T86 確實省略當日沒有法人交易的股票。例如 911868.TWSE 在 9/23 成交 210,750 股，但 T86 9/22、9/23 都沒有它；選股器卻顯示它 9/11 的 foreign_net -24,000。
   - 另外 3 列（00636K、00657K、00668K）在官方 T86 有全 0 列，但 store 在 9/22、9/23 都沒寫入。這屬於另一個問題。

5. 量級小。
   - 外資門檻 ≥1 張：1,127 檔中 42 檔是舊值；≤-1 張：1,023 檔中 53 檔。
   - ≥10 張：買方 3 檔、賣方 3 檔。
   - ≥100 張：買方 0 檔，賣方 1 檔（2601 益航 -958,846，9/22；它 9/23 在 MI_INDEX 沒有行情列，日線也停在 9/22）。
   - 以列本身日期計，落後列最大的 |foreign_net| 是 24,000 股。

6. 融資部分沒有重現。選股器母體中融資最新列早於 9/23 的只有 1 列，而且沒有早於它自己的日線日期，margin_change 為 0。

7. 5 日欄位已確認永遠是 null。combined 的來源（universe、日線欄位 symbol/date/OHLC/volume/amount/quote_ts、指標、signal_*）都不含 foreign_net_5d，所以一定走到 screener.py:436-437 的補 null。前端 TaiwanScreener.tsx 沒有使用這兩個欄位，只有 API 的 sort_by 能選到它們，選了會退化成依代號排序。

自選 9 檔都有 9/23 的法人列，不受影響。嚴重度維持 P3：只影響選股器低門檻的法人條件，錯的值都在數張以內。
    - 證據：
      - 程式碼：backend/app/taiwan/screener.py:409-421（read_latest_per_symbol；inst_date=max(date)）；:435-437（5 日欄補 null）；:234（傳入 valid_symbols）；:572-582（排序）
      - 程式碼：backend/app/taiwan/institutional_store.py:211-223（每檔取自己最大日期）；:200-202 read_all → read_range 不限日期，讀全部分區
      - 前端：frontend/src/pages/TaiwanScreener.tsx:424 只顯示 data_dates.institutional_as_of；:929-931 每列只顯示 foreign_net 張數，不顯示 institutional_date；全檔沒有 foreign_net_5d
      - 指令：cd backend && uv run python scratchpad\audit\work\verify-scrstale\verify.py（輸出 out.txt）：institutional 分區 119 個 2026-04-07→2026-09-23；[finding 視窗] 1973 / 118 / 92 / 最舊 2026-08-19；2867.TWSE 2026-08-19 foreign_net=2989250（與 finding 相同）
      - verify.py 輸出：選股器 universe 2377 檔，與日線 inner join 後 2346 列，其中有法人列 2339；落後於 9/23 的 140 列、任一淨額≠0 的 132 列、foreign_net≠0 的 103 列、最舊 2026-04-13；依類別：TPEX stock 109、TWSE stock 24、TWSE etf 6、TPEX etf 1
      - verify2.py 輸出：security_master 查無 2867（0 列）；日線 9/23 分區 TWSE 1330 列、TPEX 0 列；9/22 分區 TWSE 1331、TPEX 1009；法人日期早於該列日線日期 96 列（foreign_net≠0 的 69 列）；落後天數最大 161 日（4192.TPEX 2026-04-14，-2 股）
      - 官方比對（reference csv）：T86 9/23 1332 列、store 9/23 TWSE 1329 列，差額為 00636K/00657K/00668K（官方全 0）；TPEx 9/23 5670 列與 store 完全相同；TPEx 9/22 6146 列與 store 相同；140 列舊值中 137 列不在官方 9/23 法人資料；69 列落後的上櫃列 0 列出現在官方 TPEx 9/22
      - 官方反例：911868.TWSE 在 MI_INDEX 9/23 成交 210750 股、收 1.16，T86 9/22、9/23 都沒有它；store 最後一列 2026-09-11 foreign_net=-24000，選股器沿用這個值
      - verify3.py 輸出：foreign_net>=1000 共 1127 檔（舊值 42 檔，依列日期計 27 檔）；<=-1000 共 1023 檔（53／36）；>=10000 共 812（3／0）；<=-10000 共 647（3／1）；>=100000 共 373（0／0）；<=-100000 共 333（1／0，2601 益航 9/22 -958846，與官方 T86 9/22 相同）
      - 融資（verify2.py）：選股器母體有融資列 2217；落後於 9/23 的 1 列（2026-09-17）；落後於自己日線日期的 0 列；margin_change≠0 的 0 列
      - 自選 9 檔的 institutional_date 全部是 2026-09-23，不受影響
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-scrstale/（verify.py、verify2.py、verify3.py、out*.txt、stale_rows.csv）

## 確認正確的項目

- **T86（上市）欄位對應：外陸資（不含外資自營商）r[2..4]、投信 r[8..10]、自營商合計 r[11]、自行買賣 r[12..14]、避險 r[15..17]、合計 r[18]；單位為股**：把官方原始 JSON 餵給 TwseInstitutionalAdapter.parse_payload（backend/app/taiwan/enrichment/institutional.py:74-94），逐欄與 reference CSV 比對。9/22 有 1,336 列、9/23 有 1,332 列，不一致 0 筆；has_discrepancy 為 true 的列 0 筆
  - 來源：reference/raw/twse_t86_20260922.json 的 fields：「外陸資買進股數(不含外資自營商)…自營商買賣超股數(避險)、三大法人買賣超股數」；hints 為「單位：股」
  - 來源：scratchpad/audit/work/a07chips/app_check.out 第 2、7 行
- **TPEx dailyTrade（上櫃）欄位對應：r[2..4] 外資（不含外資自營商）、r[11..13] 投信、r[14..19] 自營商自行買賣與避險、r[20..22] 自營商合計、r[23] 合計**：原始 JSON 經 TpexInstitutionalAdapter（institutional.py:178-198）解析後與 CSV 比對：9/22 有 6,146 列、9/23 有 5,670 列，不一致 0 筆
  - 來源：scratchpad/audit/work/a07chips/app_check.out 第 3、8 行
  - 來源：manifest 的 tpex_insti column_map 與算式驗證
- **自營商合計＝自行買賣＋避險；dealer_buy、dealer_sell、dealer_net**：store 與官方終版比對，9/22、9/23 兩交易所的 dealer_buy、dealer_sell、dealer_net、proprietary、hedge 不一致都是 0 筆
  - 來源：scratchpad/audit/work/a07chips/cmp_store.out 第 11-15、28-32、62-66、79-83 行
- **外資自營商不併入 foreign_net，total_net 用 computed_net（不含外資自營商）**：全部 119 個 store 分區中 official_net 與 computed_net 不同的列都是 0，has_discrepancy 也都是 0，所以外資自營商始終為 0，不影響數值。第三方比對：Yahoo 2454 在 9/23 的外資為 496 張，store 為 496,417 股
  - 來源：backend/app/taiwan/enrichment/institutional.py:94
  - 來源：backend/app/taiwan/chip_history.py:109-110
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/institutional-trading（9/23：外資 496、投信 -18、自營商 41、合計 520）
- **法人欄位單位（股）與前端換算成張（Math.round(shares/1000)）**：以 Yahoo 為第三方，比對 2454、3034、6196 共 34 個交易日、136 個值（單位：張）。除了 finding 1 列出的 2454 在 9/16、9/17、9/22 的 6 個值之外，其餘差距都在四捨五入 0.5 張以內
  - 來源：frontend/src/pages/TaiwanStockDetail.tsx:177-183
  - 來源：https://tw.stock.yahoo.com/quote/3034.TW/institutional-trading
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW/institutional-trading
  - 來源：scratchpad/audit/work/a07chips/yahoo_cmp.py 的輸出
- **MI_MARGN（上市）欄位對應與張轉股（×1000）：融資 r[2..6]、融券 r[8..12]、註記 r[15]**：原始 JSON 經 TwseMarginAdapter 解析後，兩天各 1,297 列逐欄比對（含註記），不一致 0 筆；store 對官方 12 個欄位的不一致也都是 0
  - 來源：backend/app/taiwan/enrichment/margin.py:77-93
  - 來源：reference/raw/twse_mi_margn_20260922.json 的 fields：「買進、賣出、現金償還、前日餘額、今日餘額、次一營業日限額、買進、賣出、現券償還…」
  - 來源：scratchpad/audit/work/a07chips/app_check.out 第 4-5 行；cmp_store.out 第 106-117 行
- **TPEx margin/balance（上櫃）欄位對應：前資餘額在前、券賣 r[11] 在券買 r[12] 之前**：兩天各 919 列解析結果與 store 比對，不一致都是 0
  - 來源：backend/app/taiwan/enrichment/margin.py:175-191
  - 來源：reference/raw/tpex_margin_balance_20260922.json 的 fields：「前資餘額(張)、資買、資賣、現償、資餘額…前券餘額(張)、券賣、券買、券償、券餘額…」
  - 來源：cmp_store.out 第 128-139 行
- **融資增減＝今日餘額−前日餘額；融券增減同理**：自行獨立計算並與 store 比對，兩天兩交易所不一致 0 筆。第三方比對：Yahoo 0050 在 9/10～9/23 共 10 日的融資餘額、增減、融券餘額、增減、券資比，50 個值全部一致；2454 在 9/22、9/23 也一致
  - 來源：https://tw.stock.yahoo.com/quote/0050.TW/margin（例：2026/09/23 融資餘額 64,587、增減 -7,648、融券 691、增減 17、券資比 1.07%）
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/margin（例：9/23 融資 10,300、-58、融券 73、-7、0.71%）
- **券資比的 store 公式＝融券餘額÷融資餘額×100，四捨五入到小數 2 位；融資餘額為 0 時為 null**：用官方張數獨立計算後與 store 的 short_margin_ratio 比對，兩天各 1,297 個上市列不一致 0 筆；與 Yahoo 0050 的 10 日、2454 的 2 日完全一致
  - 來源：backend/app/taiwan/margin_store.py:130-136
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/margin 的頁面定義：「券資比 = 融券餘額 / 融資餘額 x 100%」
  - 來源：https://tw.stock.yahoo.com/news/融資餘額怎麼看…-100054359.html：「券資比 = 融券餘額 ÷ 融資餘額 × 100%」，個股用張計算
- **detail_service._institutional_5d（5 個 store 日期視窗、缺列當 0、視窗不滿 5 日回 None）**：複製 store 到 scratchpad，mock 掉 TaiwanInstitutionalStore 後呼叫 _institutional_5d，與直接讀 parquet 的獨立加總比對：6 檔自選股 × 15 個 as_of（9/03～9/23）× 3 個欄位，共 270 個值，最大差為 0。自選 9 檔在最近 60 個 store 日期都沒有缺列
  - 來源：backend/app/taiwan/detail_service.py:425-449
  - 來源：scratchpad/audit/work/a07chips/app_check.out 第 31-43 行
- **chip_history 的法人與融資歷史（20 日）**：對 store 複本呼叫 TaiwanChipHistoryService（taipei_today mock 成 9/24），抽 2454、0050、6196 各 20 列逐欄與 parquet 比對，不一致 0；券資比由獨立重算，也一致
  - 來源：backend/app/taiwan/chip_history.py:96-145
  - 來源：app_check.out 第 45-48 行
- **research_context 融資 5 日增減（最近 5 個 store 日期的 margin_change 加總）**：9 檔自選股 × 最近 10 個 as_of，比較 margin_change 加總與「今日餘額−5 個日期前的餘額」，90 例全部相同
  - 來源：backend/app/taiwan/research_context.py:585-609
- **research_context 法人 20 日累計**：讀程式碼：視窗是最近 20 個 store 日期（research_context.py:518-519），tail(20) 就是視窗內全部列（:549-553），缺列等於 0，語意與 _institutional_5d 一致。5 日口徑是已知問題，不重複回報
  - 來源：backend/app/taiwan/research_context.py:518-553
- **市場層級的融資與融券餘額、增減、全市場券資比公式**：store 加總與官方彙總表比對：上市 9/22 融資 9,245,371 張、融券 218,839 張、增減 -85,096 張；9/23 融資 9,282,264 張、融券 213,059 張、增減 +36,897 張，全部相符。上櫃合計 9/22 融資 2,335,144 張、融券 35,783 張，9/23 融資 2,347,191 張、融券 33,674 張，也全部相符
  - 來源：backend/app/taiwan/market_intelligence.py:533-548
  - 來源：reference/csv/twse_mi_margn_summary_20260922_20260923.csv、tpex_margin_balance_summary_20260922_20260923.csv
  - 來源：app_check.out 第 159-187 行
- **融資融券 21:40 排程取得的是終版**：9/23 融資分區在 21:40 寫入，與凌晨取得的官方資料 12 個欄位全部一致（融資是單一次公布，沒有初版與終版之分）
  - 來源：backend/app/jobs/daily_pipeline.py:194-198
  - 來源：cmp_store.out 第 176-187 行
  - 來源：https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm：「融資融券資料 一般可下載時間 21:00」
- **has_discrepancy 自檢邏輯（買−賣＝淨額、自營商合計＝自行買賣＋避險、合計＝各項相加）**：讀程式碼（institutional.py:95-97、199-201）；用官方資料解析後旗標全部為 false，與官方算式驗證的結果一致
  - 來源：manifest 的 validation_checks：T86 與 TPEx 的 violations 都是 0
- **StalePolicy：法人與融資在交易日 13:30 之後超過 4 個日曆日才算過期**：讀程式碼。回補的舊分區狀態為 stale，當日抓取的是 official，與 store 的 status 分布相符
  - 來源：backend/app/taiwan/enrichment/models.py:49-72

## 無法驗證的項目

- **融資 store 在 2026-04-09（1,104 列）與 2026-06-18（794 列）出現大量「前日餘額≠前一日今日餘額」的不連續；其他日期每天只有 1～19 列**：參考資料只有 9/22、9/23，沒有 4 月、6 月的官方 MI_MARGN，無法判斷是 store 分區資料有誤，還是官方當天整批調整。目前 5 日視窗不涵蓋這兩天，自選股現值不受影響
- **T86 在 16:30 之後被修正的確切原因（鉅額交易或錯帳更正）**：不得連證交所，拿不到 9/22、9/23 的鉅額交易明細（BFIAUU）。目前是推論：T86 附註寫明「含…鉅額」，鉅額逐筆交易到 17:00，上櫃投信與外資 17:30 才產製；差異型態也符合（外資買進與賣出同額增加，例如 2330 在 9/22 買進與賣出各 +1,118,000 股）
- **16:30 那班若上櫃（或上市）尚未公布、只寫入單一交易所的分區，之後該日另一交易所會永遠缺漏（institutional_margin_refresh.py:190、309 只要有任一邊有資料就寫入並標記該日已完成）**：119 個分區中上市最少 1,292 列、上櫃最少 3,862 列，沒有觀察到只寫入一邊的分區，屬推測風險
- **外幣計價 ETF（第六碼為 K、M、S、C）的融資交易單位**：9/22、9/23 的 MI_MARGN 中沒有這類代號，無法驗證 ×1000 是否適用；自選股都是新台幣計價
- **上櫃個股的第三方比對**：自選股全是上市，網路請求有上限，沒有抓上櫃個股的第三方頁面；上櫃對官方資料的比對結果見 cmp_store.out
- **research_context 法人 5 日累計（tail(5) 以列數計）**：已記錄於 docs/code-review-2026-09-22.md，依規定不重複回報
