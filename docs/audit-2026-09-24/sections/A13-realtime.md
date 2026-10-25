# A13-realtime｜盤中即時與分鐘資料

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 6 | 2 | 0 | 0 | 0 | 18 | 9 |

## 稽核者摘要

結論：MIS 欄位的數值解析正確。以 9/23 收盤後的 MIS 快照（2454、6196、0050、00878、6488）對照官方 MI_INDEX 與 TPEx 資料，z、y、o/h/l、漲跌幅、五檔價量五檔全部一致。App 自算的漲跌停價也與 MIS u/w（5 檔）、TWT49U 除權息日（7 筆）全部一致。交易時段邊界與 session_progress 的 12 個時點全部符合預期。問題集中在時段與口徑的處理：
(1) 收盤集合競價（13:30）的成交結果每天都接不到。收盤後 MIS 被標成非即時，final sync 取到的是被丟棄的官方舊資料，卻照樣記為完成。結果 13:30 到 16:30 之間，今日K、指標與分鐘K停在集合競價前的價格；自選清單則顯示正確收盤價，兩個畫面不一致。18 個「檔×日」樣本中有 11 個不符，最大差 0.54%。
(2) 盤前 08:30–09:00、平日休市日，以及報價全部過期的空窗期間，每 6 秒輪詢一次，每次都會下載一份無快取的 STOCK_DAY_ALL，下載後全部丟棄。9/18 盤前 25 分鐘內共 221 次；9/25 中秋休市估計約 3,000 次，有 IP 被限流的風險。
(3) 盤中成交量只含整股一般交易（MIS v），歷史與收盤後的量則是官方口徑（含零股、盤後定價、鉅額），兩種口徑混在同一個量比裡。9/23 收盤時四檔的量比與週轉率因此偏低 4.8%–8.2%。
(4) Yahoo 備援把最高價當成開盤價。
(5) 分鐘K的量歸屬錯位：13:25–13:30 沒有任何成交，這段的分鐘K卻有量。
(6) 時段模型把 13:25–13:30 收盤集合競價當成連續交易。
另有兩項在稽核過程中發現、屬日K領域的問題，已實證：9/16 與 9/23 的 taiwan/daily 分區完全沒有上櫃資料，9/23 enriched 也是 0 檔上櫃，原因是 16:30 抓取 TPEx 失敗卻回報 success，且之後不會回補（P1）；同日寫入的量比官方終值少，例如 2454 在 9/18 少 434,469 股。網路請求共 28 次，沒有連線到證交所或櫃買中心的任何網域。

## 問題清單

### [P1 → P1] （跨領域）9/16、9/23 日K分區完全沒有上櫃資料，TPEx 抓取失敗卻回報 success，之後也不回補（確認成立）

- **主張**：16:30 盤後更新抓 TPEx 快照失敗時只記一行 WARNING，daily 仍回報 success。21:40 那一輪看到當日分區已存在，就判定「already current」而略過，所以上櫃約 1,000 檔當日資料永久缺失。9/23 的 enriched 分區上櫃為 0 檔，而且上櫃個股往後每天的均線、KD 等遞推指標都會跳過這兩天。
- **正確的定義或數值**：任一市場抓取失敗時，應回報 partial／failed，並在下一輪自動重抓該市場該日。
- **app 實際行為**：回報 success；分區只寫入 TWSE，之後的排程判定為 current，不再重抓。
- **對使用者的影響**：自選 9 檔都是上市，不受直接影響。但選股、策略與上櫃個股的K線、指標缺兩天資料，遞推指標長期偏移。屬日K領域，請該領域複核。
- **修法建議**：把分市場的成功與否納入 daily_stats；有一個市場為空就視為 partial。resolve_missing_date_range 改為依市場判斷缺漏，並補抓 9/16、9/23 的 TPEx。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:112-118（TPEx 例外只記 warning）
  - backend/app/taiwan/daily_update.py:282-286（分區已存在即判定 current，status=success）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - taiwan/daily 分區列數：09-14/15 為 TWSE 1330＋TPEX 1006；09-16 為 TWSE 1330＋TPEX 0；09-23 為 TWSE 1330＋TPEX 0；09-21/22 的 TPEX 為 1009
  - kline_daily_enriched：09-21 上櫃 992 檔、09-22 988 檔、09-23 0 檔
  - 官方參考 tpex_trading_stock：6488 9/16 成交 6,666 張、8069 9/23 成交 5,797 張，但本地 store 沒有這兩列
  - backend.log 2026-09-23 16:30:05「TPEx snapshot fetch failed for 2026-09-23: peer closed connection」；16:30:33「overall_status=success, daily=success」；21:40:10 也回報 success，但 9/23 分區 mtime 仍是 16:30:05
  - 同一輪 share_capital 的 TPEX 抓取也失敗（16:30:20），刷新後只剩 1095 檔（推測上櫃的已發行股數隨之遺失）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：OfficialDailySnapshotAdapter.fetch_date 會把 TPEx 抓取失敗（例外、找不到表格、無資料）一律吞掉，只把 TWSE 的列寫入分區，daily 狀態仍記為 success。之後各輪的 run_update 以全庫最新分區日判斷已是最新，refresh_dates 也會略過既有分區，21:40 的補跑因此無效，也沒有任何依市場補抓的機制。

實測 2026-09-16、09-23 兩天的 taiwan/daily 與 kline_daily_enriched 都缺全部約 1,006～1,009 檔上櫃資料，但官方資料顯示這兩天都有交易。上櫃個股的 prev_close／change_pct（例如 6488 在 9/17 存成 -0.22%，官方為 -2.67%）、均線（6488 的 MA5 在 9/17 存成 909.0，官方收盤算出為 913.0）與 KD、MACD、RSI 等遞推指標都已偏移，影響會延續到 60 日視窗之後。只能用手動 CLI 的 --force --target-date 修復。

需要修正的兩點：
1. 上櫃股本並未遺失。16:30 那一輪股本檔只剩上市的 1095 檔，21:40 已補回 TPEX 893 檔。
2. 9/16 的 log 已被輪替，當天失敗原因無法確認，但任何一種原因都會落入同一條「記為 success、不回補」的路徑。

法人與融資資料在這兩天都有上櫃列，所以缺口只在日 K。
    - 理由：主要主張成立，嚴重度維持 P1。

程式碼部分：snapshot_provider.py:112-118 會吞掉 TPEx 抓取的例外，只記一行 WARNING。在 fetch_tpex 裡，找不到表格（228-230）或解析後沒有資料（282-283）時，一樣會安靜地回傳空的 DataFrame。結果 fetch_date 只拿到 TWSE 的 frame，也照常回傳。daily_refresh.py:173-177 只要 df 不是空的就會寫入分區，而且把 dates_fetched 加 1，failed_dates 仍是空的，所以 daily_update.py:308-311 會把狀態記為 success。

之後的排程輪次：daily_update.py:282-286 用全庫最新分區日（d_max）判斷，d_max 已經等於目標日，resolve_missing_date_range 就回傳 None，結果回報「Daily store already current」並記為 success。就算真的進到 refresh_dates，daily_refresh.py:167-170 只要日期已經有分區就略過。

daily_pipeline.py:194-195 的註解寫明，21:40 那一輪的用意是「讓 16:30 那班若失敗…仍有第二次機會」。可是分區已經存在，這個用意就落空了。data_integrity.py 只掃 kline_daily 等 A 股族表，也只看分區有沒有缺，不會檢查單一市場有沒有缺。app 裡沒有任何路徑會依市場補抓。唯一能修的是手動 CLI：--force --target-date。

資料與 log 部分：
- 本地實測 taiwan/daily 的上櫃列數：09-16 與 09-23 為 0，前後日期是 1006～1009。kline_daily_enriched 的上櫃列數：09-16、09-23 為 0。
- 官方參考資料顯示，這兩天 TPEx 都有正常交易：6488、8069 有成交，櫃買指數也有值。
- backend.log 2026-09-23 16:30:05 有 TPEx 例外；16:30:33 回報 daily=success；21:40 那一輪沒有對 MI_INDEX／dailyQuotes 發出任何請求，也回報 success。9/23 分區的 mtime 仍是 16:30:05。

實際影響（遞推指標已確實偏移）：6488.TPEX 在 09-17 的 prev_close 存成 914（這是 9/15 的收盤）。官方 9/16 收盤是 937，所以 change_pct 存成 -0.22%，實際應為 -2.67%。MA5 在 09-17、18、21、22 存成 909.0／914.4／920.2／934.4，官方收盤算出來是 913.0／921.4／927.6／939.0。作為對照，09-15 兩邊都是 917.6，代表算法本身一致。依 Wikipedia 的定義，SMA 是「previous k data-points」的平均。缺兩根 K 棒時，視窗會往前多吃舊資料。這個影響會延續到 ma60、high_60d 這類 60 日視窗，而 EMA、MACD、KD 這類遞推指標則是長期帶著偏差。

需要修正的附帶說法：
1. finding 推測「上櫃已發行股數隨之遺失」不成立。21:40 那一輪 TPEx 的股本重抓成功（log 21:40:02 記錄 TPEX 893 筆、合計 1988 筆）。現在 share_capital.parquet 有 TWSE 1095、TPEX 893，只在 16:30～21:40 之間暫時缺上櫃股本。
2. 9/16 的 log 已經被輪替掉（backend.log.2 不存在，backend.log.3 從 9/17 11:14 才開始），所以無法確認當天是例外、找不到表格還是回應為空。但這三種情況，程式碼都會記成 success，而且不會回補。
3. 同一輪的法人與融資兩個 store 在 9/16、9/23 都有上櫃資料，缺口只出現在日 K OHLCV 與後續算出的 enriched。

使用者情境：自選 9 檔都是上市，不受直接影響。但全市場選股、策略、訊號與上櫃個股的 K 線，都會拿到錯的漲跌幅與指標。這屬於「錯誤的金融口徑」，而且沒有自動修復路徑。資料沒有被覆寫破壞，也沒有直接導致錯誤成交，所以不到 P0，維持 P1。這個問題不在 code-review-2026-09-22.md 已記錄的清單裡；B8、B10 是指數月資料，屬於不同路徑。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-118：TPEx 例外只記 logger.warning("TPEx snapshot fetch failed ...")，fetch_date 照常回傳 TWSE-only frame；同檔 228-230、282-283 找不到表或無資料時也靜默回傳空 frame
      - backend/app/taiwan/daily_refresh.py:173-177：只要 df 非空就 write_batch 並 dates_fetched += 1，不區分市場；167-170：`if cur in available` 直接略過既有分區
      - backend/app/taiwan/daily_update.py:282-286：d_range 為 None 時 status="success"、note="Daily store already current"；308-311 failed_dates 為空即 success
      - backend/app/jobs/daily_pipeline.py:194-195 註解：21:40「讓 16:30 那班若失敗…仍有第二次機會；已落盤的日期會被 refresh 略過」——部分市場分區同樣被略過，第二次機會失效
      - backend/app/services/data_integrity.py:44 只掃 kline_daily／kline_etf_daily／kline_index_daily，且只判分區缺失或盤中快照，不檢查市場別列數
      - 本地計算（uv run python ...\work\verify_tpex_gap\counts.py）：taiwan/daily date=2026-09-15 TWSE 1330 TPEX 1006；09-16 TWSE 1330 TPEX 0；09-17 TPEX 1006；09-22 TPEX 1009；09-23 TWSE 1330 TPEX 0。kline_daily_enriched 09-16 TPEX 0、09-21 992、09-22 988、09-23 0
      - 官方參考 csv\tpex_trading_stock_202608_202609.csv：6488 2026-09-16 成交 6666 張、收盤 937.0、漲跌 +23；2026-09-17 收盤 912.0、漲跌 -25；8069 2026-09-23 成交 5797 張；index_daily csv：TPEX_INDEX 2026-09-16 收 399.21、2026-09-23 收 413.76（兩日皆為交易日）
      - 本地計算（impact.py）：6488.TPEX enriched 2026-09-17 prev_close=914.0、change_pct=-0.002188（官方應為 (912-937)/937=-2.67%）；ma5 在 09-17/18/21/22 = 909.0/914.4/920.2/934.4，依官方收盤計算 = 913.0/921.4/927.6/939.0；09-15 兩者皆 917.6
      - data/backend.log:37259 `2026-09-23 16:30:05,341 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body`；37265 `16:30:33 ... overall_status=success, daily=success`；45470-45477 21:40 輪次無 MI_INDEX／dailyQuotes 請求，仍 `daily=success`
      - data\taiwan\daily\date=2026-09-23\part.parquet mtime 2026-09-23 16:30:05.394（21:40 未改寫）；date=2026-09-16 mtime 2026-09-16 16:30:22；9/16 當日 log 已輪替（backend.log.3 起於 2026-09-17 11:14），9/16 失敗原因無直接 log 證據
      - 修正附帶證據：backend.log:45474-45476 `21:40:02 taiwan share capital: TPEX returned 893 rows ... refreshed: 1988 symbols`；目前 share_capital.parquet TWSE 1095、TPEX 893——上櫃股本並未永久遺失
      - 本地計算（inst.py）：institutional 與 margin 在 2026-09-16、09-23 皆有 TPEX 列（例如 margin 09-23 TPEX 919），缺口僅限日K
      - 線上定義來源 https://en.wikipedia.org/wiki/Moving_average：「a simple moving average (SMA) is the unweighted mean of the previous k data-points」，SMA_k=(p_(n-k+1)+…+p_n)/k——缺漏交易日會使視窗跨入更早資料（Investopedia 無法抓取；Yahoo／HiStock／鉅亨個股歷史頁回 404 或不存在，抓不到）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：16:30 盤後更新時，TPEx 快照抓取失敗只會記一行 WARNING（snapshot_provider.py:112-118），只有 TWSE 的資料照常寫入分區，而且計為 dates_fetched，daily 回報 success。之後的排程只拿最大日期判斷缺漏（daily_update.py:124-147、282-286），refresh_dates 也以日期為單位略過已存在的分區（daily_refresh.py:167-177），所以該日的上櫃資料永遠不會重抓。

9/23 的經過有 log 可以證明：16:30:05 TPEx 抓取失敗；21:40 那一輪沒有發出任何日K請求，判定 success 且 is_fully_current=True。9/16 的分區同樣只有 TWSE，但當天的 log 已經不在，當時也還沒有 21:40 班次；它沒被回補，是因為隔天那一輪從 9/17 開始抓。

結果是 9/16 與 9/23 的 taiwan/daily 與 kline_daily_enriched，上櫃都是 0 檔。官方資料顯示，這兩天約有 1,000 檔上櫃股有成交，例如 6488 在 9/16 成交 6,666 張、8069 在 9/23 成交 5,797 張。上櫃個股的 MA、EMA、MACD、KD 等指標都跳過這兩天計算，例如 6488 在 9/22 的 MA5，本地 934.4、官方 939.0。缺口隔天的 prev_close 與 change_pct 也拿兩天前的收盤價計算：6488 在 9/17 的漲跌幅，本地 -0.22%、官方 -2.67%，漲跌停與相關訊號也會跟著錯。MA 的偏差在窗口移出缺口後會恢復，遞推指標的偏差則會逐漸衰減。

share_capital 的上櫃資料在 16:30:20 被整檔覆寫成只剩 TWSE 1095 檔，但 21:40 已恢復為 1988 檔（上櫃 893 檔，與官方相符），不是永久遺失。
    - 理由：我寫了自己的腳本重新計算，finding 裡的數字全部重現：9/16、9/23 日K分區的上櫃筆數為 0，enriched 9/23 上櫃為 0，6488 與 8069 在兩天的官方成交量存在、本地卻沒有這兩列。log 顯示 9/23 16:30 抓 TPEx 失敗，這一輪仍回報 success；21:40 那一輪完全沒有對 MI_INDEX／dailyQuotes 發出請求，日K判定 success，而且 is_fully_current=True。程式路徑也對得上：snapshot_provider 把 TPEx 的例外吞掉，只剩 TWSE 的 df 會被寫入並計為 dates_fetched；resolve_missing_date_range 只看最大日期；refresh_dates 以日期為單位略過已存在的分區，所以該日永遠不會再按市場重抓。

需要修正或補充的地方有五點：
(1) 9/16 找不到 log 可以佐證。backend.log.2 不存在，backend.log.3 從 9/17 11:14 才開始，所以 9/16 的成因只能從分區 mtime 16:30:22、只有 TWSE 推測是同一機制。另外 21:40 班次是 9/18 的 commit 484afc8 才加上的，9/16 當天只有 16:30 班；9/16 沒被回補的原因是隔天那一輪從 d_max+1（9/17）開始抓，不是 21:40 略過。
(2) enriched 9/16 的上櫃也是 0 檔，finding 沒提到。
(3) share_capital 的上櫃資料不是永久遺失。16:30:20 確實只寫入 1095 檔，但 21:40:02 已重新整理為 1988 檔；目前檔案上櫃 893 檔，與官方 mopsfin_t187ap03_O 的 893 筆相同。缺口只持續約 5 小時，不過 refresh() 在單一交易所失敗時會整檔覆寫、丟掉另一市場的舊資料，這一點屬實。
(4) 還有 finding 沒寫到的影響：缺口隔天的 prev_close 與 change_pct 是拿兩天前的收盤價算的。6488 在 9/17 的漲跌幅，本地是 -0.22%，官方是 -2.67%；以 prev_close 為基礎的漲跌停、訊號判斷也會跟著錯。
(5) 均線偏差的期間有上限：MA_N 在窗口移出缺口後就會恢復，例如 MA60 要 60 個交易日。EMA、MACD、KD 這類遞推指標的偏差則會逐漸衰減，但不會完全歸零。

嚴重度維持 P1：全市場約 1,000 檔上櫃股缺兩天日K，屬於錯誤的金融口徑，而且系統不會自動回補。使用者自選的 9 檔都是上市，不受直接影響。
    - 證據：
      - 分區計數（腳本 AUDIT_SCRATCH/work/verify-tpexgap/count_partitions.py）。taiwan/daily：09-14、09-15 為 TPEX 1006＋TWSE 1330；09-16 只有 TWSE 1330；09-21、09-22 為 TPEX 1009＋TWSE 1331；09-23 只有 TWSE 1330，與 finding 完全一致
      - kline_daily_enriched：09-21 為 TPEX 992／TWSE 1327；09-22 為 TPEX 988／TWSE 1328；09-23 只有 TWSE 1323。finding 沒提到的 09-16 也只有 TWSE 1319，上櫃為 0
      - 官方 tpex_trading_stock_202608_202609.csv 與本地比對（compare_ref.py）：6488 在 9/16 的官方收盤 937、成交 6,666 張，9/23 收盤 950、28,715 張；8069 在 9/16 收盤 148、3,921 張，9/23 收盤 144、5,797 張。本地 daily 與 enriched 在這四列都是 None；其他日期，例如 6488 在 9/22 的收盤 971、成交 18,780,685 股，與官方的 18,781 張相符
      - 官方 tpex_daily_quotes_20260923.csv：本地 9/22 的 1009 個上櫃代號中，有 1007 個出現在官方 9/23 行情，其中 997 個成交量大於 0，可證明約 1,000 檔當日有交易卻缺漏
      - 分區 mtime：daily/date=2026-09-23/part.parquet 為 2026-09-23 16:30:05.394、33,106 bytes；date=2026-09-16 為 2026-09-16 16:30:22、32,978 bytes；正常分區約 54,5xx bytes
      - data/backend.log:37258 記錄 16:30:05「TPEx snapshot fetch failed for 2026-09-23: peer closed connection…」；37266 記錄 16:30:33「overall=success, daily=success」
      - backend.log:45470-45478 是 21:40 那一輪：沒有任何 MI_INDEX／dailyQuotes 請求；結束時記錄「overall_status=success, daily=success … is_fully_current=True」
      - backend/app/taiwan/providers/snapshot_provider.py:112-118：TPEx 例外只記 warning，然後照常 concat TWSE
      - backend/app/taiwan/daily_update.py:282-286：d_range 為 None 時設為 success、note 為「Daily store already current」；同檔 124-147 的 resolve_missing_date_range 只比對最大日期。工作樹的 git diff 在此檔沒有內容差異
      - backend/app/taiwan/daily_refresh.py:167-177：日期已在 available 就略過；只要 df 非空就寫入並 dates_fetched+=1，不檢查市場是否齊全
      - backend/app/jobs/daily_pipeline.py:194-195：註解宣稱 21:40 班次是 16:30 失敗時的第二次機會，但遇到部分寫入的日期會直接略過，這個設計意圖因此失效。21:40 班次由 commit 484afc8（2026-09-18 00:06）加入，9/16 當天還不存在
      - 9/16 沒有 log 可查：backend.log.3 從 2026-09-17 11:14:36 開始，而 backend.log.2 不存在
      - indicator_gap.py 以本地 enriched 對照官方收盤序列重算：6488 在 9/22 的 ma5，本地 934.4、官方 939.0；ma20 本地 945.6、官方 943.45。9/17 的 prev_close 本地 914、官方 937，change_pct 本地 -0.22%、官方 -2.67%。8069 在 9/17 的 prev_close 本地 146.0、官方 148.0。9/14、9/15 缺口前的 ma5、ma20 與官方完全相同，可證明偏差來自缺口
      - share_capital：backend.log:37263 記錄 16:30:20 只刷新到 1095 檔；45475 記錄 21:40:02 刷新到 1988 檔（TPEX 893）。目前 share_capital.parquet 的 TPEX 893／TWSE 1095，與官方 tpex_mopsfin_t187ap03_O 的 893 筆一致。share_capital_store.py:106-136 在單一交易所失敗時會整檔覆寫，造成約 5 小時的暫時缺口

### [P2 → P2] 收盤集合競價（13:30）結果從未進入即時路徑：今日K、指標、分鐘K在 13:30–16:30 停在集合競價前的價量（確認成立）

- **主張**：13:30:00 之後 MIS 報價被標為 official_snapshot（is_realtime=False），被 hybrid 的即時閘門丟棄；改走官方 STOCK_DAY_ALL 取得的舊資料，又被 _records_datable_today 丟棄。_fetch_quotes 只看 _fetched_at 是否前進，所以 final sync 仍記為完成並停止輪詢。MinuteBarRecorder.record 也以牆鐘時間是否為 OPEN 作閘門，13:30 那筆成交永遠進不去。結果今日列停在最後一次盤中輪詢（集合競價前），直到 16:30 盤後更新才被官方值取代；自選清單同一時間卻直接顯示 MIS 的收盤價。原記錄第一節只提到兩者閘門不對稱，沒有指出這件事每天收盤都必然發生。
- **正確的定義或數值**：收盤後至少應有一次 post-close 快照把官方收盤價與累計量寫入今日列與最後一根分鐘K，final sync 只有在真正寫入後才算完成。
- **app 實際行為**：最後寫入的是 13:29 前的集合競價前價量；final sync 以「取到任何資料」判定成功。
- **對使用者的影響**：每個交易日 13:30–16:30，預覽卡片、K線、指標、量比顯示的收盤價與清單不同（差 0.1%–0.5%）；分鐘圖缺最後一筆成交與集合競價量。如果 16:30 排程失敗，錯誤會持續更久。
- **修法建議**：POST_CLOSE 時段如果 MIS 的 d＝今天且 t≥13:30:00，視為今日最終快照，允許寫入日K並折算進分鐘K（或在 flush 前補上 13:30 那根）；final sync 的成功條件改為「今日列已用收盤快照更新」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/mis_provider.py:208-213、238（非 OPEN 就不是 realtime）
  - backend/app/taiwan/providers/hybrid_provider.py:178-183、222-225（丟棄非即時，改走 official.fetch_quote）
  - backend/app/services/quote_service.py:603-611、625-635、844-857、863（fetched_at 前進就算 final 成功）
  - backend/app/taiwan/realtime/minute_recorder.py:81-82（record 只在 OPEN 時折算）
  - backend/app/api/kline.py:533、586（活蠟燭覆蓋今日列）
  - backend/app/taiwan/watchlist_enrichment.py:208、240（清單直接取 q.last_price）
  - backend/app/jobs/daily_pipeline.py:180（16:30 才更新）
- **線上證據**：
  - https://rich01.com/pricing-before-market-open/：「收盤前試搓時間：13:25~13:30」「試搓期間並不會有買賣單成交」
  - https://support.fugle.tw/trading/trading-dictionary/9648/：「整股盤中收盤 13:25-13:30」
  - https://tw.stock.yahoo.com/quote/2454.TW：9/23 成交 5,185（資料時間 14:30）
- **本地計算證據**：
  - verify_mis.py：模擬 13:30:05 時，5 檔 MIS 報價全部 is_realtime=False，_live_quote_frame 保留 []；record() 回傳 0，未完成的K棒收盤仍是 5180、量 0；13:30:00 那筆（5185，560 張）被丟棄
  - log 2026-09-23：13:28:53 剩 8 檔、13:29:00 剩 6 檔、13:29:06 剩 1 檔，13:29:12 起全部改走 STOCK_DAY_ALL，且沒有「enriched 增量」；13:30:08「close final sync completed」
  - 分鐘K最後一根的收盤價 vs 官方收盤價：9/18 為 2454 4730/4710、3034 555/558、6196 539/540、2379 726/727、00878 34.9/34.83、00885 18.55/18.58、00893 43.65/43.64；9/23 為 2454 5180/5185、3034 543/545、2379 759/761、00893 46.21/46.19。18 個樣本中 11 個不符，最大差 3034 −0.54%
  - 集合競價量沒進分鐘K：MIS tv 顯示 2454 有 560 張（占 v 的 9.5%）、0050 1532 張、00878 622 張、6196 71 張
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立：13:30 收盤集合競價的結果從未進入即時寫入路徑。

1. 13:30:00 之後，MIS 報價因為牆鐘已非 OPEN，被標成 official_snapshot（is_realtime=False），hybrid 把它丟掉。
2. 改走 STOCK_DAY_ALL 取得的資料，status 只會是 official 或 stale，又被 _records_datable_today 丟掉。
3. _fetch_quotes 只看 _fetched_at 有沒有前進，所以 final sync 在沒有寫入今日列的情況下仍判為成功並停止輪詢。
4. MinuteBarRecorder 同時有 status=="realtime" 與牆鐘 OPEN 兩道閘門，13:30 那筆成交與集合競價量進不去。

結果：
- 今日日K、enriched 指標與卡片活蠟燭停在集合競價撮合前最後一筆通過 60 秒新鮮度閘門的報價（9/23 為 13:28:53–13:29:06），直到 16:30 盤後更新成功才被官方值取代；自選清單則直接顯示 MIS 的收盤價。
- 分鐘K的缺口是永久的：沒有 13:30 的K棒、最後收盤價錯、缺少集合競價量。沒有任何流程會回補，所以不只影響 13:30–16:30。
- 只要 13:30 時後端在跑且即時行情開啟，每個交易日都會發生。畫面上能看出差異的比例，在 9/18、9/23 兩天的樣本中是 11/18，最大差距約 0.54%。

嚴重度維持 P2。
    - 理由：我逐段重讀了現況程式碼，並用本地模擬、log、分鐘K實際資料與官方參考資料做了驗證。finding 描述的因果鏈全部成立。

一、規則面：
- zh.wikipedia〈臺灣證券交易所〉寫明「盤中(09:00-13:25)原則上採逐筆交易」「收盤(13:25-13:30)集合競價」。
- 官方參考資料 raw/mis_snapshot.json 的 2454 為 d=20260923、trade.t=13:30:00、z=5185、tv=560。STOCK_DAY 同日收盤也是 5185。
- 因此官方收盤價就是 13:30:00 那一筆集合競價的成交價，MIS 要到 13:30 之後才會發布。
- en.wikipedia 仍寫「09:00 to 13:45」，資料已過時，所以不採用。

二、程式碼面：
1. 行情狀態只看牆鐘。calendar.get_market_status 只在 t<=13:30:00 時回 OPEN，之後一律是 POST_CLOSE。mis_provider 用抓取當下的牆鐘決定 status，所以收盤後抓到的報價 status=official_snapshot、is_realtime=False。
2. hybrid._live_quote_frame 丟掉 is_realtime=False 的報價，改走 official.fetch_quote（STOCK_DAY_ALL）。官方資料的 status 只會是 official 或 stale，所以再被 _records_datable_today 丟掉。
3. 即使這樣，_process_full_market_records 仍會先更新 _fetched_at。_fetch_quotes 只比較 _fetched_at 有沒有前進，於是 final sync 被記為成功並停止輪詢。
4. 分鐘K有兩道閘門。_record_minute_bars 只收 status=="realtime" 的列；MinuteBarRecorder.record 在牆鐘不是 OPEN 時直接回傳 0。
5. 清單走 watchlist_enrichment，直接取 rt_service.get_quotes 的 q.last_price，不經過上述閘門。
6. kline 的活蠟燭用 enriched 快取中今天的那一列覆蓋今日列。這份快取要到 16:30 的 refresh_enriched_cache 才重新載入；日K更新失敗時會跳過重載。

三、本地模擬（sim_close.py，用 mock 的 MIS 回應，不連網）：
- 牆鐘設在 13:30:05 時，4 檔報價全部 is_realtime=False，kept_by_live_gate 都是 False。
- record() 回傳 0。
- 官方資料列的 status='official'，不論日期是昨天或今天，_records_datable_today 保留 0 筆。

四、log（data/backend.log，9/23）：
- 13:28:53、13:29:00、13:29:06 的即時列逐步從 8 檔、6 檔減到 1 檔。這是 60 秒新鮮度門檻造成的。
- 13:29:12 起全部改走 STOCK_DAY_ALL，而且再也沒有「enriched 增量」。
- 13:30:03 最終同步開始，13:30:08 STOCK_DAY_ALL，接著「close final sync completed」。之後多次重啟也都一樣。
- 16:30:38 才出現 enriched cache refreshed。

五、分鐘K實際資料（cmp_minute.py）：
- 9/18 與 9/23 兩天後端有跑過收盤。最後一根分鐘K落在 13:26–13:28，沒有 13:29 或 13:30 的K棒。
- 最後收盤價與官方收盤價在 18 個樣本中有 11 個不符（9/18 有 7 個，9/23 有 4 個）。最大差距是 3034 在 9/18 的 −0.538%。

六、是否為新問題：原記錄第一節只談兩條路徑的閘門不對稱，A7 只談收盤後迷你K不做修補，都沒有提到「集合競價結果每天都無法寫入今日列與分鐘K，而且 final sync 在沒寫入的情況下仍判為成功」。所以這是新面向，不算重複。

七、嚴重度：維持 P2。今日列與指標的錯誤是暫時的，16:30 由官方值取代；使用者有 0 條監控規則，所以不影響告警。沒有造成資料破壞，也不是永久錯誤的金融口徑，因此不到 P1。

八、需要修正的細節：
1. 今日列最後寫入的時間點是 13:28:53–13:29:06。那已經在收盤集合競價時段（13:25–13:30）內，只是還沒撮合，所以比較精確的說法是「停在集合競價撮合前最後一筆通過新鮮度閘門的報價」。
2. 分鐘K不只受影響到 16:30。台股沒有任何流程會回補 kline_minute（provider.get_minute 回傳空表，16:30 的排程也不處理分鐘資料），所以缺少 13:30 那根K棒與集合競價量是永久缺口。
3. 「每天必然發生」是就機制而言：只要 13:30 時後端在跑且即時行情開啟就一定會發生。畫面上看得出差異，則要看收盤價是否不同於撮合前最後一筆報價，樣本中約 6 成會不同。
4. watchlist_enrichment 取 close 的位置是第 239 行，finding 寫第 240 行。其餘行號都與現況相符。
    - 證據：
      - backend/app/taiwan/realtime/calendar.py:118-121：`elif t <= REGULAR_OPEN_END: return MarketStatus.OPEN else: return MarketStatus.POST_CLOSE`（REGULAR_OPEN_END=13:30:00，第 23 行）
      - backend/app/taiwan/realtime/mis_provider.py:119-121、208-213、238：用抓取當下的牆鐘判斷狀態，非 OPEN 就標成 OFFICIAL_SNAPSHOT，且 `is_realtime=(current_market_status == MarketStatus.OPEN and not is_stale)`
      - backend/app/taiwan/providers/hybrid_provider.py:178（`if q.last_price is None or not q.source_meta.is_realtime: continue`）、222-225（改走 official.fetch_quote）
      - backend/app/taiwan/providers/official_provider.py:89 與 taiwan_values.py:29-37：官方報價的 status 只會是 'official' 或 'stale'
      - backend/app/services/quote_service.py:1068-1070：`return [r for r in records if r.get("status") == "realtime"]`；第 863、870 行的日K寫入都經過這道閘門
      - backend/app/services/quote_service.py:845-851 先設定 `self._fetched_at = fetched_at`；第 635 行 `return self._fetched_at > before`；第 603-611 行 ok 就加入 _final_sync_done、flush 分鐘K並停止輪詢
      - backend/app/services/quote_service.py:1535 `live = [r for r in records if r.get("status") == "realtime"]`；minute_recorder.py:81-82 `if get_market_status() != MarketStatus.OPEN: return 0`
      - backend/app/taiwan/watchlist_enrichment.py:208、239：清單直接用 rt_service.get_quotes 的 q.last_price；kline.py:533、586-595：活蠟燭以 enriched 快取中今天的列覆蓋今日列
      - backend/app/jobs/daily_pipeline.py:169-175、180：16:30 日K更新成功後才執行 refresh_enriched_cache；台股沒有任何程式回補 kline_minute（grep 結果：只有 minute_recorder 寫入）
      - 線上來源 https://zh.wikipedia.org/wiki/臺灣證券交易所：「盤中(09:00-13:25)原則上採逐筆交易」「收盤(13:25-13:30)集合競價」（https://en.wikipedia.org/wiki/Taiwan_Stock_Exchange 仍寫 09:00 to 13:45，已過時，不採用）
      - 官方參考資料 reference\raw\mis_snapshot.json：2454 `"d":"20260923"`、`"trade":{"t":"13:30:00","v":560,"z":"5185.0000"}`；reference\csv\twse_stock_day_202608_202609.csv 的 2454 在 2026-09-23 收盤 5185.0
      - 本地模擬 `uv run python ...\work\verify_close_auction\sim_close.py`：牆鐘 13:30:05 時 2454/6196/0050/00878 全部 `status=official_snapshot is_realtime=False kept_by_live_gate=False`；`MinuteBarRecorder.record ... returned 0`；官方資料列的 `status='official' -> kept by _records_datable_today: 0`
      - data\backend.log 9/23：35325 行 13:28:53 剩 8 檔、35329 行 13:29:00 剩 6 檔、35333 行 13:29:06 剩 1 檔（最後一次「enriched 增量」在 35336 行）；35337 行起全部改走 STOCK_DAY_ALL；35370 行 13:30:03 最終同步開始；35371 行 STOCK_DAY_ALL；35373 行 13:30:08 close final sync completed；37350 行 16:30:38 enriched cache refreshed
      - 本地計算 `uv run python ...\work\verify_close_auction\cmp_minute.py`（分鐘K最後收盤 vs 官方收盤）：9/18 為 2454 4730/4710（+0.425%）、3034 555/558（−0.538%）、6196 539/540、2379 726/727、00878 34.9/34.83、00885 18.55/18.58、00893 43.65/43.64；9/23 為 2454 5180/5185、3034 543/545、2379 759/761、00893 46.21/46.19。18 個樣本中 11 個不符；兩天最後一根K棒都在 13:26–13:28（UTC 05:26–05:28），沒有 13:30 的K棒
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：現行程式碼（f43c7bd 之後）確實存在這個問題，而且每個交易日收盤都會發生。13:30:00 收盤集合競價的結果在 MIS 上一定是在牆鐘 13:30:00 之後才出現，此時已是 POST_CLOSE，被標為 official_snapshot、is_realtime=False，因此被 hybrid 即時閘門丟棄。改走 STOCK_DAY_ALL 取得的列 status=official，又被 _records_datable_today 丟棄。_fetch_quotes 只看 _fetched_at 是否前進，所以仍記為 final sync 完成並停止輪詢。MinuteBarRecorder 也因牆鐘不是 OPEN 而丟掉 13:30 那筆。

結果如下：
- 分鐘K的最後一根停在 13:26–13:28，也就是試撮時段內最後一次被判為即時的快照。價格是試撮價還是 13:25 前最後成交價，尚未驗證（推測）。收盤集合競價的價與量都沒有進入分鐘K，例如 2454 在 9/23 少了 560 張，占 MIS 累計量的 9.49%。
- 若程序從盤中一路跑過收盤，今日列與指標停在同一筆快照；若在 13:30 後重啟或重載（9/23 13:40 起即是如此），今日列根本不存在。兩種情況都要等到 16:30 官方更新才會修正。
- 自選清單同一時間直接顯示 MIS 的收盤價。

以 9/18 與 9/23 共 18 個樣本計算，分鐘K最後收盤價有 11 個與官方收盤價不符，絕對差 0.02%–0.54%（finding 寫的「0.1%–0.5%」要修正），最大是 3034 在 9/18 的 −0.538%。9/18 跑的是舊版程式，當天的今日列其實是前一日收盤（屬於已修的另一個問題），所以 9/18 只能當分鐘K的樣本。
    - 理由：我從數據重現的角度逐項重算，finding 列出的每個數值都完全對得上。
(1) 分鐘K最後一根收盤價與官方 STOCK_DAY 收盤價的比較：
- 9/18：2454 4730/4710、3034 555/558、6196 539/540、2379 726/727、00878 34.9/34.83、00885 18.55/18.58、00893 43.65/43.64。
- 9/23：2454 5180/5185、3034 543/545、2379 759/761、00893 46.21/46.19。
- 以上與 finding 一致。9/18 另有 0050、3545 相符，9/23 另有 0050、00878、00885、3545、6196 相符，合計 18 個樣本、11 個不符。最大差距是 3034 在 9/18 的 −0.538%。
- 兩天的最後一根K棒都在台北 13:26–13:28，沒有 13:29 或 13:30 的K棒。

(2) 官方 MIS 快照顯示 13:30:00 那筆成交：2454 z=5185、tv=560、v=5902，560/5902=9.49%；0050 tv=1532、00878 tv=622、6196 tv=71，與 finding 相同。

(3) 我自己寫腳本把 9/23 的 MIS 原始回應餵進現行程式碼：
- 牆鐘 13:29:59 時，5 檔都是 realtime，is_realtime=True，_live_quote_frame 取得 5 列。
- 牆鐘 13:30:05 時，5 檔都變成 official_snapshot，is_realtime=False，但 last_price 仍是 5185；_live_quote_frame 只剩 0 列。
- get_market_status 在 13:30:00.000 回 OPEN，13:30:00.5 已經是 POST_CLOSE。
- _records_datable_today 會丟掉 status=official 的列。
- MinuteBarRecorder.record 在 POST_CLOSE 時回傳 0，進行中的K棒停在 5180；把閘門打開做對照，則會產生 13:30、收盤 5185、量 560 張的K棒。

(4) 9/23 的 log 與 finding 一致：
- 13:28:53 剩 8 檔、13:29:00 剩 6 檔、13:29:06 剩 1 檔，每次後面都跟著「enriched 增量」。
- 13:29:12 起每次都呼叫 STOCK_DAY_ALL，而且沒有任何「enriched 增量」。
- 13:30:03 最終同步開始，13:30:08 寫出「close final sync completed」。
- 官方 9/23 分區在 16:30:05 才建立。
- 程式碼方面，_fetch_quotes 只比較 _fetched_at 是否前進；_process_full_market_records 在寫日K之前就先更新 _fetched_at；自選清單直接取 q.last_price，不看 is_realtime。以上都屬實。

有 4 點需要補充修正，但都不推翻結論：
(a) user_impact 寫的「差 0.1%–0.5%」不精確。11 個不符樣本的實際絕對差是 0.02%–0.54%（例如 00893 在 9/18 只差 +0.023%）。
(b) 最後寫入的快照時間在 13:26–13:28，已經落在 13:25–13:30 的試撮時段內。依 rich01 的說法，這段時間不會成交，所以「集合競價前的價量」精確地說是「試撮時段內最後一筆被判為即時的快照」。它的價格是試撮價還是 13:25 前最後一筆成交，我沒有驗證（推測）。
(c)「今日列停在最後一次盤中輪詢」只在程序從盤中一路跑過收盤時成立。9/23 在 13:40:52 之後後端多次重載，重載後 enriched 快取日期仍是 2026-09-22，最終同步也沒有寫入，所以這段期間K線根本沒有今日列（缺列，而不是舊值），直到 16:30。兩種情況都一樣拿不到官方收盤價。
(d) 9/18 跑的是舊版程式：_records_datable_today 在 9/22 19:36 的 f43c7bd 才加入。當天收盤後還會把 STOCK_DAY_ALL 的前一日資料寫成今日列，例如 kline_daily/date=2026-09-18 的 2454 收盤 4500、量 6,495,763，正是 9/17 的官方值。所以 9/18 只能當分鐘K的樣本，不能代表現行程式的今日列行為。finding 只把 9/18 用在分鐘K比較，沒有誤用。

「每天收盤都必然發生、final sync 誤判完成、分鐘K缺 13:30 那筆」是原記錄沒寫到的新面向。原記錄第 189–195 行只提到收盤後迷你K棒不做即時修補。嚴重度維持 P2。
    - 證據：
      - 本地計算：cd backend && uv run python AUDIT_SCRATCH/work/verify-closeauction/last_bar.py → 9/18 最後一根K棒（UTC 05:26–05:28，即台北 13:26–13:28）與官方收盤比較：2454 4730/4710 (+0.425%)、3034 555/558 (−0.538%)、6196 539/540 (−0.185%)、2379 726/727 (−0.138%)、00878 34.9/34.83 (+0.201%)、00885 18.55/18.58 (−0.161%)、00893 43.65/43.64 (+0.023%)，0050 與 3545 相符；9/23：2454 5180/5185 (−0.096%)、3034 543/545 (−0.367%)、2379 759/761 (−0.263%)、00893 46.21/46.19 (+0.043%)，其餘 5 檔相符 → 18 個樣本中 11 個不符，與 finding 完全一致
      - 本地計算：tail_bars.py → 9/18 與 9/23 都沒有 13:29／13:30 的K棒；9/23 的最後一根K棒是 13:27／13:28，量 0，收盤 2454=5180
      - 官方參考資料 reference/csv/mis_snapshot.csv（MIS 9/23 快照，t=13:30:00）：2454 z=5185.0、tv=560、v=5902 → 560/5902=9.49%；6196 z=549、tv=71；0050 z=112.45、tv=1532；00878 z=35.09、tv=622
      - 官方參考資料 reference/csv/twse_stock_day_202608_202609.csv：9/23 收盤 2454=5185、3034=545、2379=761、00893=46.19；9/18 收盤 2454=4710、3034=558
      - 本地模擬 sim_close.py：get_market_status(13:30:00.000)=OPEN，(13:30:00.5)=POST_CLOSE；牆鐘 13:30:05 時 5 檔 MIS 報價 status=official_snapshot、is_realtime=False、last_price 仍是 5185，TaiwanHybridProvider._live_quote_frame 只剩 0 列（13:29:59 時為 5 列）；QuoteService._records_datable_today([status=official]) 回傳 []；MinuteBarRecorder.record(13:30:00 5185/5902 張，POST_CLOSE) 寫出 0 根，進行中的K棒 close=5180；對照組把閘門打開則產生 13:30、收盤 5185、量 560000 股的K棒
      - data/backend.log:35325-35373：13:28:53 8 只、13:29:00 6 只、13:29:06 1 只（每次都有 enriched 增量）；13:29:12 起都呼叫 STOCK_DAY_ALL 且沒有 enriched 增量；13:30:03 最终行情同步开始；13:30:08,909 close final sync completed
      - data/backend.log:35413,35443：13:40:52 重載後 enriched 快取日期是 2026-09-22 → 收盤後重啟時今日列根本不存在（不是舊值）
      - backend/app/taiwan/realtime/calendar.py:118-121（t <= 13:30:00 算 OPEN，之後為 POST_CLOSE）；mis_provider.py:208-213、238（非 OPEN 就標 official_snapshot 且 is_realtime=False）
      - backend/app/services/quote_service.py:625-635（_fetch_quotes 回傳 _fetched_at > before）；quote_service.py:844-857（先更新 _fetched_at，再用 _records_datable_today 過濾後寫日K）；quote_service.py:1056-1071（只有 status==realtime 才會寫入）；quote_service.py:1528-1543（分鐘K只收 realtime）；minute_recorder.py:81-82（非 OPEN 直接回傳 0）
      - backend/app/taiwan/providers/official_provider.py:84-90（STOCK_DAY_ALL 的列 status=official）；watchlist_enrichment.py:208、240（清單直接用 q.last_price）
      - 資料時間：data\taiwan\daily\date=2026-09-23\part.parquet 建立於 2026-09-23 16:30:05；data\kline_minute\date=2026-09-23 修改時間 13:30（收盤 flush）
      - git log -S：_records_datable_today 在 f43c7bd（2026-09-22 19:36）加入 → 9/18 仍是舊版；data\kline_daily\date=2026-09-18 的 2454 close=4500、volume=6495763，正是 9/17 官方值（舊版會把前一日值寫成今日列，現已修）
      - 線上來源 https://rich01.com/pricing-before-market-open/ 原文：「收盤前試搓時間：13:25~13:30」「這些買賣單並不會在試搓期間成交」

### [P2 → P2] 盤前、平日休市日與報價全部過期的空窗，每 6 秒下載一次無快取的 STOCK_DAY_ALL，結果全部丟棄（確認成立）

- **主張**：MIS 報價只要不是即時，hybrid 就改呼叫 official.fetch_quote，每次整包下載 openapi 的 STOCK_DAY_ALL，沒有快取。取回的資料因 status 不是 realtime，一律被 _records_datable_today 丟棄。_market_phase 只排除週末、不知道國定假日，所以休市日會在 08:30–13:30 持續輪詢，並因 date_mismatch 每輪都走備援。
- **正確的定義或數值**：非即時時段不應反覆下載一份不會被使用的全市場檔；休市日不應輪詢。
- **app 實際行為**：每輪都整包下載後丟棄，且完全不認得國定假日。
- **對使用者的影響**：不影響資料正確性，但會對 openapi.twse.com.tw 產生大量無效請求（使用者本來就擔心 IP 被限流），也浪費頻寬與 CPU。
- **修法建議**：盤前與休市日直接略過官方備援（反正不會寫入），或按交易日快取 STOCK_DAY_ALL；_market_phase 改用已知休市日曆（例如由 TWSE 休市表或 TaiwanTradingCalendar 載入）。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/services/quote_service.py:1216-1226（只判斷 weekday）、1236-1244
  - backend/app/taiwan/providers/hybrid_provider.py:216-227
  - backend/app/taiwan/providers/official_provider.py:23、41-47（每次都發 GET、無快取）
  - backend/app/taiwan/realtime/mis_provider.py:193-200（休市日會觸發 date_mismatch→STALE）
  - backend/app/taiwan/realtime/models.py:51（60 秒門檻）
- **線上證據**：
  - https://www.wantgoo.com/global/holiday/tw：「2026/09/25 (五) 中秋節」「2026/10/09 (五) 國慶日」「2026/10/26 (一) 台灣光復…補假」
- **本地計算證據**：
  - log 2026-09-18 盤前：08:30–08:55 共 221 次 STOCK_DAY_ALL，每分鐘 7–9 次
  - log 2026-09-23：10:11:07–10:11:56 有 7 次、13:29:12–13:30:08 有 8 次（全部報價過期後的備援）
  - 推測：一個平日休市日約 300 分鐘×10 次/分≈3,000 次 STOCK_DAY_ALL 加上等量的 MIS 請求；9/25 就是下一個休市日
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：台股模式下，MIS 這一批報價只要沒有任何一筆是即時且未過期的，TaiwanHybridProvider.get_realtime 就會改呼叫 official.fetch_quote。會發生這種情形的時段有：交易日盤前 08:30–09:00（MIS 在 PRE_OPEN 一律不算即時）、休市日，以及盤中整批報價超過 60 秒的時候。每次都會新建 provider 和 httpx.Client，直接 GET 整份 openapi STOCK_DAY_ALL，完全沒有快取。取回的資料 status 只會是 official 或 stale，所以被 _records_datable_today 和 _record_minute_bars 全部濾掉，不會寫進日K或分K；唯一的作用是更新抓取時間與檔數，以及讓 close_final 被判定為成功。

_market_phase 只排除週末，程式裡也沒有任何休市日來源。因此像 2026/9/25（中秋節）這種平日休市日，08:30–13:30 仍會照常輪詢；MIS 的日期與當天對不上，每輪都判定為 stale，於是每輪都走備援。實測每分鐘約 8–9 次，一個休市日約 2,400–2,700 次 STOCK_DAY_ALL，另外還有等量的 MIS 請求（推測，前提是當天後端有開著）。

此外盤中也不只是偶發空窗：2026-09-23 盤中 1041 輪輪詢裡，有 374 輪（約 36%）走了這條備援並把結果丟棄。依人事行政總處的辦公日曆，接下來的平日休市日為 9/25、10/9、10/26；9/28 教師節在行政曆上也放假，但證交所當天是否休市未查證。這個問題不影響資料正確性，屬於明顯的無效外部請求，加上休市日照常輪詢這個功能錯誤，嚴重度維持 P2。
    - 理由：我逐段讀過引用的程式碼（行號與 finding 一致，沒有偏移），也做了一次離線模擬：禁止所有 socket 連線，MIS 餵參考資料裡的快照，並把 OfficialTaiwanAdapter._json 換成只計數的樁函式。另外核對了 log，也查了官方休市日曆。finding 的主要主張全部成立。

(1) 盤前一定走備援：MIS 只有在 MarketStatus.OPEN 且報價沒過期時，才會設 is_realtime（mis_provider.py:238）。所以 08:30–09:00 的每一筆 MIS 報價都不是即時。hybrid 的 _live_quote_frame 會把這些全部濾掉（hybrid_provider.py:178-183），一整批都被濾掉時，就改呼叫 official.fetch_quote（hybrid_provider.py:222-225）。

(2) 沒有快取：registry.get_provider 每次都新建一個 TaiwanHybridProvider（registry.py:16），它每次又新建 OfficialTaiwanAdapter 和新的 httpx.Client（hybrid_provider.py:44、official_provider.py:39）。_json 每次都直接發 GET，沒有任何快取（official_provider.py:41-47）。

(3) 取回的資料實際上用不到：official_status 只會回 official 或 stale（taiwan_values.py:29-37），_records_datable_today 只放行 status 為 realtime 的列（quote_service.py:1068-1070），_record_minute_bars 也一樣只收 realtime（quote_service.py:1535）。模擬結果是每輪 4 筆、status 為 official、能寫進今日日K的是 0 筆。

(4) 不認得休市日：_market_phase 只判斷週末（quote_service.py:1217-1218），_DEFAULT_CALENDAR 也沒有預載任何休市日（calendar.py:164），repo 裡找不到任何休市日來源。模擬 9/25 10:00 得到 phase=regular，08:40 得到 preopen，兩者都會輪詢。MIS 在非交易時段回傳的是前一交易日的資料（參考快照在 9/24 01:53 抓取，d=20260923、t=13:30:00）。套進 mis_provider 後，因 date_mismatch 全部標成 stale，每輪都下載 1 次 STOCK_DAY_ALL。報價新鮮（10 秒內）時，下載次數是 0，對照組正常。

(5) log 佐證：9/18 08:30–08:55 共 221 次，每分鐘 7–9 次。9/23 10:11 有 7 次，13:29–13:30 有 8 次，而且這些輪次後面都沒有 enriched 增量，與丟棄的行為一致。

需要修正或補充的地方：
(a) finding 把盤中的狀況寫成偶發的「空窗」，實際低估了。9/23 09:00–13:30 共 1041 輪行情刷新，其中 374 輪（約 36%）都走 STOCK_DAY_ALL 並被丟棄，而且大致每隔一分鐘集中出現一次。
(b) 休市日的推估：預設輪詢間隔是 6 秒，實測每分鐘 7–9 次，所以一天約 300 分鐘 × 8–9 ≈ 2,400–2,700 次，再加上等量的 MIS 請求和收盤後 1 次。finding 寫的「≈3,000」略高，不過數量級正確。
(c) 休市日清單：人事行政總處 115 年辦公日曆列出的平日放假日有 9/25、9/28（教師節）、10/9、10/26，finding 引用的玩股網清單沒有 9/28。9/28 當天證交所是否休市，我沒有交易所的來源可以確認。
(d) 備援取回的資料並非百分之百沒用：它仍會更新 _fetched_at 與檔數、觸發 SSE 廣播，還會讓 close_final 被判定為成功。不過行情本身不會寫入，也不會被拿去評估。

嚴重度：這不影響資料正確性，但有兩個問題。一是盤前、盤中約 36% 的輪次、以及休市日一整天，都在做沒有用的全市場下載，屬於 CONTRIBUTING.md 第 11 節定義的「明顯性能退化」。二是休市日照常輪詢，屬於「明確功能錯誤」。所以維持 P2。上述情形是否實際發生，取決於後端在那些時段有沒有開著。log 顯示後端連日運作，9/24 05:19 仍在執行，所以 9/25 很可能會發生，但這一點是推測。
    - 證據：
      - backend/app/services/quote_service.py:1217-1218：`if now.weekday() >= 5: return "closed"`，_market_phase 除了週末之外沒有任何休市判斷；1236-1244 preopen/regular 一律輪詢
      - backend/app/taiwan/realtime/calendar.py:164：`_DEFAULT_CALENDAR = TaiwanTradingCalendar()` 沒有預載休市日；對 app/ 執行 grep holiday，沒有找到任何休市日資料來源
      - backend/app/taiwan/realtime/mis_provider.py:194-200 date_mismatch→is_stale；:238 `is_realtime=(current_market_status == MarketStatus.OPEN and not is_stale)`，盤前一律 False
      - backend/app/taiwan/providers/hybrid_provider.py:178-183 濾掉非即時與 stale；:216-225 live 為空時呼叫 self.official.fetch_quote
      - backend/app/data_providers/registry.py:16 `return provider_cls()` 每輪新建 provider；hybrid_provider.py:44 新建 OfficialTaiwanAdapter；official_provider.py:39 新建 httpx.Client；:41-47 每次都直接 GET，沒有快取
      - backend/app/taiwan/providers/taiwan_values.py:29-37 official_status 只回 'official'/'stale'；quote_service.py:1068-1070 _records_datable_today 只放行 status=='realtime'；quote_service.py:1535 分K也只收 realtime
      - backend/app/services/preferences.py:78 預設 realtime_quote_interval=6.0；使用者的 preferences.json 沒有覆寫這個值
      - 離線模擬（scratchpad\audit\work\verify_stockdayall_poll\sim.py，封鎖 socket，MIS 用參考快照）：2026-09-25T10:00 phase=regular、MIS 全部為 ('2026-09-23','stale',False,True)，3 輪共下載 STOCK_DAY_ALL 3 次、每輪 datable_today=0；09-25T08:40 phase=preopen，結果相同；交易日盤前 d=今天時為 official_snapshot/is_realtime False，仍然 3 次；盤中 t 超過 90 秒時 3 次；t 在 10 秒內時 0 次
      - 參考資料 raw\mis_snapshot.json（2026-09-24 01:53 抓取）：d=20260923 t=13:30:00，非交易時段回傳的是前一交易日
      - log data/backend.log.1：`grep '^2026-09-18 08:[3-5]' | grep -c STOCK_DAY_ALL` = 221，每分鐘 7–9 次
      - log backend.log：2026-09-23 09:00–13:30 共 1041 次『行情刷新』，STOCK_DAY_ALL 374 次，『enriched 增量』667 次；10:11:07–10:11:55 共 7 次，13:29:12–13:30 共 8 次，這些輪次都沒有 enriched 增量
      - 線上（非 finding 來源）人事行政總處 115 年辦公日曆 CSV https://www.dgpa.gov.tw/FileConversion?filename=dgpa/files/202507/0e62ddf8-c983-44b7-b0ea-c190a142cdd0.csv...：列出 2026/9/25 中秋節、2026/9/28 教師節、2026/10/9 補假、2026/10/26 補假（All Day Event TRUE）
      - 線上 https://zh.wikipedia.org/zh-tw/中華民國節日：中秋節、孔子誕辰紀念日／教師節（9月28日）、臺灣光復暨金門古寧頭大捷紀念日（10月25日）都是放假日；「若與星期六重疊，則星期五補假一天；若與星期日重疊，下星期一即補假一天」
      - 線上 https://www.wantgoo.com/global/holiday/tw：只列出 2026/09/25、10/09、10/26，沒有 9/28；9/28 當天證交所是否休市，沒有交易所來源可以確認（未查證）
      - docs/code-review-2026-09-22.md 的 A1（第 52 行，盤前落到 Yahoo）與 B3（第 294 行，寫入端）沒有涵蓋重複下載 STOCK_DAY_ALL 或休市日輪詢，屬於新問題
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：只要 9 檔自選股都沒有即時報價，hybrid 就改呼叫 official.fetch_quote，每一輪都整包下載 openapi 的 STOCK_DAY_ALL，沒有任何快取。這包括每個交易日的盤前 08:30–09:00（MIS 盤前一律不算即時）、盤中所有報價超過 60 秒的空窗、13:30 後的最終同步，以及平日休市日（MIS 報價日期不符，被標成 STALE）。下載回來的 status 是 official，現行程式（f43c7bd 之後）會用 _records_datable_today 全數濾掉，不寫日 K、enriched 或分鐘 K；但仍會更新 _fetched_at、廣播 SSE，並讓收盤最終同步被標成完成。_market_phase 只排除週末，app 內也沒有載入任何休市日，所以 9/25 中秋節會在 08:30–13:30 持續輪詢，每輪都走官方備援。實測：9/18 08:30–08:55 共 221 次（每分鐘 7–9 次，間隔中位數 6.74 秒）；9/23 10:11 有 7 次、13:29–13:30 有 8 次；9/23 整個盤中有 374 次，占全部 1041 輪的 36%。一個平日休市日推估約 2,300–2,700 次 STOCK_DAY_ALL，加上幾乎等量的 MIS 請求（finding 原本的約 3,000 次略為高估）。9/18 當時還沒有 datable 閘門，那些資料其實被寫成了當日 K 棒，所以 9/18 的 log 只能證明請求頻率。
    - 理由：我用本地 log 重算了次數，也用離線模擬重跑了整條路徑，finding 的機制和每個實測數字都能重現，所以判定 confirmed。另外有幾點要補充或修正：休市日的次數估計略高；9/18 那段 log 只能證明請求頻率，不能證明資料被丟棄；盤中的實際量比 finding 引用的兩段時間大很多。

【程式碼確認】
- `_market_phase` 只排除週末：quote_service.py:1217。
- app 內沒有任何地方把假日加進 TaiwanTradingCalendar，grep `add_holiday|known_holidays=` 找不到呼叫點。
- MIS 盤前一律 `is_realtime=False`（mis_provider.py:238）。休市日會因 date_mismatch 被標成 STALE（mis_provider.py:194-200）。
- hybrid 在沒有任何即時報價時，改呼叫 `official.fetch_quote`（hybrid_provider.py:178、182、223）。
- 每次呼叫都會直接 GET STOCK_DAY_ALL，沒有快取（official_provider.py:23、41-47、56）。
- 回傳資料的 status 是 `official`，會被 `_records_datable_today` 濾掉（quote_service.py:1056-1070）。
- realtime service 的第 4 層 `official_close_provider.get_quote`（service.py:159）呼叫的方法根本不存在，`hasattr` 為 False，所以會被 except 吞掉。因此輪詢路徑上真正發出 STOCK_DAY_ALL 請求的只有 hybrid 這一處。

【數據重現：finding 的數字 vs 我算出的數字】
- 9/18 08:30–08:55：finding 221 次；我算 221 次（08:30:06.627 至 08:55:31.586）。
  - 每分鐘：finding 7–9 次；我算完整分鐘 7–9 次，最後不完整的 08:55 為 5 次。
  - 相鄰請求間隔中位數 6.74 秒（最小 5.79、最大 12.07）。
  - 每次請求都剛好對應一輪「行情刷新」（221/221）。
- 9/23 10:11:07–10:11:56：finding 7 次；我算 7 次。
- 9/23 13:29:12–13:30:08：finding 8 次；我算 8 次。第 8 次在 13:30:08.001，正好是收盤最終同步那一輪。
- 這些時段都沒有 MIS 或 Yahoo 失敗的警告，表示 MIS 有回應，只是報價不算即時。
- 輪詢間隔：preferences 沒有設定 `realtime_quote_interval`，所以用預設 6.0 秒；台灣模式沒有檔位，最低也是 6 秒。實際週期是 6 秒加上抓取耗時。

【離線模擬（simulate.py，不連網）】
MIS 餵 mock JSON，Yahoo 用 stub 取代，官方端點用會計數的 httpx MockTransport。
- 9/25 08:45：phase=preopen，會輪詢。MIS 9 檔全是 (stale, False, True)，原因是「Quote trade date 2026-09-24 does not match current session date 2026-09-25」。live 0 列，呼叫 3 次產生 3 次 STOCK_DAY_ALL GET，datable 保留 0 筆。
- 9/25 10:00：phase=regular，結果同上，3 次呼叫產生 3 次 GET，保留 0 筆。
- 9/25 13:31：phase=close_final，1 次 GET。
- 9/24 08:45（一般交易日盤前）：3 次 GET，保留 0 筆。
- 9/24 10:00，所有報價都超過 60 秒：3 次 GET，保留 0 筆。
- 對照組：9/24 10:00 即時報價，0 次 GET，保留 27 筆。
- 對照組：9/26 週六，phase=closed，不輪詢。
- 同一批呼叫中，MIS 因為 3 秒 TTL 快取只打了 1 次，官方端點卻打了 3 次，證明官方這條路完全沒有快取。

【需要修正或補充的地方】
1. 休市日的次數估計：finding 用每分鐘 10 次算出約 3,000 次（標為推測）。按實測間隔 6.74–7.9 秒計算，300 分鐘約 2,300–2,700 次 STOCK_DAY_ALL，加上幾乎等量的 MIS 請求；13:30 後還有 1 次最終同步。
2. 9/18 那段 log 早於 `_records_datable_today` 的加入時間（f43c7bd，2026-09-22 19:36）。當時 221 輪中有 218 輪之後出現「enriched 增量」，表示前一日收盤價反而被寫成當日 K 棒；這個問題已另行修正。所以 9/18 只能證明請求頻率。「資料全部丟棄」要看現行程式與 9/23 的 log：9/23 09:00–13:30 共 374 次備援，沒有一次之後出現 enriched 寫入。
3. 盤中的量遠比 finding 引用的兩段大：9/23 盤中 1041 輪裡有 374 輪（36%）走官方備援。另外，每個交易日的 08:30–09:00 都固定約 220 次，不是只有休市日才會發生。
4. 「全部丟棄」精確地說是：不寫日 K、enriched 或分鐘 K。但這些資料仍會更新 `_fetched_at`、廣播 SSE，並讓收盤最終同步被標成完成（quote_service.py:607、635）。

國定假日有兩個網站獨立確認：wantgoo 與 HiStock 都列 2026/09/25（五）中秋節休市。嚴重度依 CONTRIBUTING.md:296 的「明顯性能退化」維持 P2。docs/code-review-2026-09-22.md 裡沒有 STOCK_DAY_ALL 或休市輪詢的相關記錄。
    - 證據：
      - backend/app/services/quote_service.py:1217 `if now.weekday() >= 5: return "closed"`，除此之外沒有任何假日判斷；app 內 grep `add_holiday|known_holidays=` 找不到呼叫點（只有 calendar.py:59 的定義和 calendar.py:177 的參數傳遞）
      - backend/app/taiwan/realtime/mis_provider.py:194-200 date_mismatch→is_stale；:238 `is_realtime=(current_market_status == MarketStatus.OPEN and not is_stale)`，所以盤前一律不是即時
      - backend/app/taiwan/providers/hybrid_provider.py:178、182 過濾非即時與過期報價；:223 live 為空時呼叫 official.fetch_quote
      - backend/app/taiwan/providers/official_provider.py:23、41-47、56 每次都對 STOCK_DAY_ALL 發 GET，沒有快取
      - backend/app/services/quote_service.py:1070 `return [r for r in records if r.get("status") == "realtime"]`；:244 DEFAULT_INTERVAL = 6.0；preferences.json 沒有 realtime_quote_interval 鍵
      - backend/app/taiwan/realtime/service.py:159 呼叫 TaiwanOfficialQuoteProvider.get_quote，但該方法不存在（模擬輸出 `has get_quote: False`），因此輪詢路徑上唯一的 STOCK_DAY_ALL 下載來源是 hybrid
      - 本地計算 log_scan.py（scratchpad\audit\work\verify-sdaall\）：2026-09-18 08:30:00–08:59:59 STOCK_DAY_ALL=221，first 08:30:06.627、last 08:55:31.586，完整分鐘每分 7–9 次，間隔中位數 6.74 秒；2026-09-23 10:11:07–10:11:56 = 7 次；13:29:12–13:30:08.001 = 8 次（最後一次緊接『close final sync completed』）
      - 本地計算 pairing.py：9/18 盤前 221 次，其中 218 次之後有 enriched 寫入（早於 datable 閘門）；9/23 09:00–13:30 共 374 次 STOCK_DAY_ALL，0 次之後有 enriched 寫入，全天共 1041 輪輪詢
      - git log -S _records_datable_today：f43c7bd 2026-09-22 19:36:04 引入；hybrid 的備援路徑自 96892bf（2026-09-17）起未變
      - 離線模擬 simulate.py：9/25 08:45 phase=preopen、9/25 10:00 phase=regular，兩者 should_poll=True；MIS 報價全為 (stale, False, True)，原因是 trade date 2026-09-24 與 2026-09-25 不符；get_realtime 呼叫 3 次產生 3 次 STOCK_DAY_ALL GET，datable 保留 0 筆；對照組即時報價 0 次 GET，週六 phase=closed
      - https://www.wantgoo.com/global/holiday/tw：「2026/09/25 (五) - 中秋節」「2026/10/09 (五) - 國慶日」「2026/10/26 (一) - 台灣光復…補假」
      - https://histock.tw/holiday.aspx：「2026/09/25（星期五）- 中秋節」，與 wantgoo 一致

### [P2 → P2] （跨領域）同日寫入的日K成交量低於官方終值（確認成立）

- **主張**：當天 16:19–17:03 寫入的 taiwan/daily 分區，部分個股的量比官方 STOCK_DAY 少；隔日才寫入的分區則完全一致。推測 16:30 抓取時還沒納入之後才申報的交易（例如鉅額），之後也沒有更正。這會讓 vol_ma5 與 vol_ratio_5d 的分母偏小。
- **正確的定義或數值**：日K成交量應與官方終值一致，或在官方更正後回補。
- **app 實際行為**：維持第一次抓到的偏低值。
- **對使用者的影響**：量比、均量、週轉率的歷史基準偏差最多約 4%（2454）。屬日K領域，請該領域複核成因。
- **修法建議**：隔日再抓一次前一交易日的資料並覆寫（或在 21:40 那一輪一併重抓當日）；成因需另外查證（推測是鉅額或盤後申報較晚）。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/jobs/daily_pipeline.py:180（16:30 抓取）
  - backend/app/taiwan/daily_update.py:282-286（分區存在即不重抓）
- **線上證據**：
  - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?interval=1d&range=1d：regularMarketVolume 6425491（等於本地 store 的值，而非官方的 6,430,491）
- **本地計算證據**：
  - vol_hist.py 算出的 store − STOCK_DAY：2454 為 9/16 −249,000、9/17 −19,000、9/18 −434,469（−4.2%）、9/22 −68,000、9/23 −5,000；6196 為 9/17 −165,000、9/18 −3,000；0050 與 00878 無差；收盤價全部一致
  - 分區 mtime：有差異的是 9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30；無差異的 9/14、9/15、9/21 都是隔日寫入
  - enriched 9/23 的 2454 vol_ratio_5d 為 0.6969，用官方量獨立計算是 0.6855；6196 為 1.4324 vs 1.3946
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：當天盤後寫入的 taiwan/daily 分區（實測 16:19–17:03 由 MI_INDEX 寫入）會固定留下官方尚未彙總完成的成交量；這些成交多數不在盤中價格區間內，或成交在收盤價，推測是鉅額或其他盤後申報項目。同一個 MI_INDEX 端點日後的值等於 STOCK_DAY 與鉅亨的終值，但 21:40 那班與之後各輪都因為分區已存在而不重抓（daily_update.py:282-286、daily_refresh.py:167），所以短少的量永遠不會補上。偏差幅度比 finding 說的大：自選股 6196 在 9/17 少了 16.95%，2330 在 9/18 少了 9.05%，2454 最多少 4.19%。已存入的 vol_ratio_5d 因此偏高約 1–3%（2454 在 9/23 為 0.6969，應為 0.6860）。另外，同一個「分區存在即不重抓」的機制，加上 fetch_date 吞掉 TPEx 的例外，讓 9/16 與 9/23 整個上櫃市場的日K永久缺失，而 daily 仍回報 success；這是更嚴重的新面向，建議另立 finding。
    - 理由：主張成立，嚴重度 P2 恰當。但 finding 低估了偏差幅度，成因也只能說一部分成立。

1. 程式碼（已親自讀過）
- 16:30 的排程只抓一次全市場快照（MI_INDEX）。
- 21:40 那班雖然也帶 refresh_daily=True，但 d_range 是從「store 最新日的下一天」起算，refresh_dates 又會略過 available_dates 裡已有的日期，所以當日分區一旦存在，就再也不會重抓。
- backend.log 證實這一點：9/23 21:40 那輪只花 10 秒，完全沒有對 MI_INDEX 發請求，日K被判定「already current」。
- 程式碼裡找不到其他會用 force 重抓的排程路徑。

2. 資料比對（我自己重跑的腳本，不是沿用 finding 的 vol_hist.py）
- 當天寫入的分區有差異：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30。
- 較晚寫入的分區全部與 STOCK_DAY 一致：9/1–9/15 在 9/16 11:21 寫入，9/21 在 9/22 10:55 寫入。
- 所有收盤價都一致。

3. 同一端點前後兩次取得的值不同，這是最直接的證據
- 9/23 的分區由 MI_INDEX 在 16:30:02 寫入（backend.log:37256）。store 裡 2454 是 6,425,491。
- 參考資料裡同一個 MI_INDEX（隔日 01:5x 抓取）顯示 6,430,491，等於 STOCK_DAY。
- 所以是官方端點本身在 16:30 之後補上了成交量，不是兩個資料集口徑不同。

4. 正確的定義
- MI_INDEX 與 STOCK_DAY 的官方附註都寫「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」。所以日K成交量的官方口徑是含鉅額的全日總量。
- 我另外用一個和 finding 不同的線上來源（鉅亨 cnyes charting API）核對 2454，得到 6430.491／12131.543／10365.174／6495.763／6640.865（單位：張）。這與官方終值逐日相同，與 store 不同。

5. 成因（部分推測）
- 用「金額差 ÷ 量差」反推缺漏成交的均價：9/23 的 2454 是 5,018.13，低於當日最低價 5,140。盤中一般交易不可能成交在這個價位，所以缺的是場外申報型的成交（鉅額最可能）。
- 但 9/18 在 17:03 抓取時仍然短少，而且缺漏部分的均價剛好等於收盤價（4710、727、558、51.6、540）。可見官方彙總的延遲會超過 17:00，不能只用「16:30 早於鉅額配對結束」解釋。
- 至於確切是鉅額還是其他盤後申報項目，我無法連網查到規則原文（WebSearch 額度已用盡，維基百科、MoneyDJ、Bing、DDG 都沒有取得時段資料），所以這部分維持推測。

6. 對使用者的影響（比 finding 說的大）
- finding 說最多約 4%，實測遠大於此：
  - 自選股 6196 在 9/17 少了 165,000 股，占 16.95%；
  - 2330 在 9/18 少了 3,702,177 股，占 9.05%；
  - 2454 在 9/18 少了 4.19%。
- 已存入的 enriched 分區裡，9/23 的 vol_ratio_5d：2454 為 0.6969（官方應為 0.6860；finding 寫 0.6855，略有出入），6196 為 1.4324（應為 1.3946），2330 為 0.9763（應為 0.9667）。
- 比率偏差約 1–3%，會連帶影響 vol_ma5／vol_ma10、turnover_rate 與 signal_volume_surge（門檻 2.0）的邊界判斷。
- 這屬於資料抓取時機造成的錯值，不是公式口徑錯，對交易結果的影響有限，維持 P2。

7. 同一機制的新面向（原 finding 與 code-review-2026-09-22.md 都沒提到）
- 9/16 與 9/23 兩個分區只有 1,330 筆 TWSE 資料，整個 TPEx（約 1,000 檔）缺席。
- 9/23 16:30 抓 TPEx 時連線中斷（backend.log:37258），fetch_date 吞掉這個例外（snapshot_provider.py:113-118），daily 仍回報 success，之後因為分區已存在而永不重抓。
- 使用者的自選股全部是上市股，所以不受直接影響，但選股與策略的全市場計算會少掉上櫃這兩天的資料。
- 這一項比原 finding 嚴重，建議另立 finding 由日K領域評級。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:180 16:30 CronTrigger 抓日K；:194-198 21:40 班次的註解自己寫明「已落盤的日期會被 refresh 略過, 不重複下載」
      - backend/app/taiwan/daily_update.py:282-286 d_range=resolve_missing_date_range(d_max, target)，無缺日就回報 'Daily store already current'；:124-146 從 d_max+1 起算
      - backend/app/taiwan/daily_refresh.py:150,167-170 available_dates 內的日期直接 dates_skipped，不重抓
      - data/backend.log:37256 '2026-09-23 16:30:02 GET .../MI_INDEX?date=20260923'；backend.log:45469-45479 9/23 21:40:00→21:40:10 整輪沒有 MI_INDEX 請求，daily=success
      - 本地比對（cmp.py，store 對 STOCK_DAY）：9/16 分區 mtime 16:30:22，2454 為 −249,000；9/17 16:19:15，6196 為 −165,000、2454 為 −19,000；9/18 17:03:37，2454 為 −434,469、2330 為 −3,702,177；9/22 16:30:03，2454 為 −68,000；9/23 16:30:05，2454 為 −5,000；9/1–9/15（9/16 11:21 寫入）與 9/21（9/22 10:55 寫入）完全一致；收盤價全部一致
      - 同一端點不同時間：參考資料 raw/twse_mi_index_20260923.json（2026-09-24 01:5x 抓取）的 2454 成交股數 = 6,430,491；store 9/23（16:30 由同一個 MI_INDEX 寫入）= 6,425,491
      - 官方定義：MI_INDEX 報價表與 STOCK_DAY 的 notes 都有「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」（參考資料 raw 檔）
      - 線上獨立來源：https://ws.api.cnyes.com/ws/api/v1/charting/history?resolution=D&symbol=TWS:2454:STOCK&from=1790208000&to=1789430400 回傳 v = [6430.491, 12131.543, 11237.165, 10365.174, 6495.763, 6640.865, 6209.254]（9/23→9/15），與 STOCK_DAY 一致，與 store 不一致
      - 反推缺漏成交的均價（implied.py）：9/23 的 2454 為 25,090,650 ÷ 5,000 = 5,018.13，低於當日最低價 5,140 → 不是盤中一般交易；9/18 在 17:03 抓取，缺漏均價等於收盤價（2454 為 4710.0，2379 為 727.0，3034 為 558.0，3545 為 51.6，6196 為 540.0）
      - 幅度（implied.py）：6196 在 9/17 為 −16.95%；2330 在 9/18 為 −9.05%，9/22 為 −5.08%；2454 在 9/16 為 −3.75%，9/18 為 −4.19%
      - enriched 分區 kline_daily_enriched/date=2026-09-23 的 vol_ratio_5d：2454 為 0.6969（官方量重算 0.6860），6196 為 1.4324（1.3946），2330 為 0.9763（0.9667），3034 為 0.8493（0.8462）；公式見 app/indicators/pipeline.py:436,490（volume ÷ 前 5 日均量）
      - 新面向：store 9/16、9/23 分區只有 ('TWSE',1330)、沒有 TPEX；backend.log:37258 'TPEx snapshot fetch failed for 2026-09-23: peer closed connection...'；snapshot_provider.py:113-118 吞掉例外，daily 仍回報 success
      - 腳本與輸出：AUDIT_SCRATCH/work/verify_volfinal/（cmp.py、out.txt、implied.py、ratio.py）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：當日 16:19～17:03 寫入的 taiwan/daily 上市分區（9/16、9/17、9/18、9/22、9/23），部分個股的成交量與成交金額低於官方終值，收盤價則一致。隔日才寫入的分區與官方完全一致。

成因：同一個 MI_INDEX 端點在寫入之後才補進成交資料。9/24 01:53 重抓 9/23 已與 STOCK_DAY 相同。晚到的量多半是整數千股，且成交價常落在當日盤中高低之外，推測是鉅額交易。寫入後的 21:40 那一輪與之後的排程都因「分區已存在」而不重抓，所以偏低值會永久保留。

影響範圍比原 finding 大：
- 9/23 全市場上市 1,330 檔中有 23 檔偏低，個股最多 −99%（6464：2,300 對 302,300）。
- 自選股中 2454（最多 −4.19%）、6196（9/17 −16.95%）、3034（最多 −1.46%）、2379（−1.25%）、3545（−0.63%）受影響；ETF 無差。

對指標的影響：
- 當日量偏低，所以當天的 vol_ratio_5d 偏低（6196 9/17：0.5723 對 0.6891）。
- 之後幾天因為 vol_ma5 與分母偏小，量比偏高（2454 9/23：0.6969 對 0.6860）。
- 已知有爆量訊號被漏發：2330 在 9/18 本地是 1.93，官方量算出 2.11，門檻 ≥2.0，signal_volume_surge 應為 true 卻記成 false。

同一機制的另一個後果：9/16 與 9/23 的上櫃抓取失敗後仍寫出只有上市的分區，狀態記為 success。之後同樣不重抓，這兩天全部上櫃個股的日K與 enriched 都缺，建議另外立案。
    - 理由：我用官方參考資料和本地 parquet 重新計算，finding 列出的差異數字全部重現，連個位數都相同：
- 2454：9/16 −249,000、9/17 −19,000、9/18 −434,469（−4.19%）、9/22 −68,000、9/23 −5,000。
- 6196：9/17 −165,000、9/18 −3,000。
- 0050 與 00878 無差。
- 380 列收盤價全部一致。

分區寫入時間也相符：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30。隔日才寫入的 8/03～9/15 和 9/21，對 10 檔官方資料零差異。

成因上我拿到比 finding 更直接的證據，排除了「口徑不同」的可能。本地來源是 TWSE MI_INDEX（snapshot_provider.py:33,129）。參考資料在 9/24 01:53 重抓同一端點，9/23 的 2454 是 6,430,491 股，與 STOCK_DAY 相同。所以差異來自同一來源在 16:30 之後才補進的資料，不是兩個資料集定義不同。

全市場 9/23 共 1,330 列上市資料比對，其中 23 列偏低，0 列偏高，收盤價 0 差：
- 偏低最嚴重的例子：6464 本地 2,300 對官方 302,300（−99.2%）、1109 −89%、1227 −78%。
- 從差額反推的成交價常落在當日盤中高低之外，例如 1109 12.15（最低 13.35）、2882 123.0（最高 112.5）、2454 5018.13（最低 5140）。成交量多是整數千股。MI_INDEX 附註寫明「本統計資訊含一般、零股、盤後定價、鉅額交易」。這些都支持「晚到的是鉅額交易」。
- 鉅額交易的申報時段沒能上網查證（WebSearch 額度已用完，3 次 WebFetch 都沒有相關內容），這部分仍屬推測。
- 9/18 在 17:03 寫入時仍缺，可見 17:03 也還太早。21:40 那一輪是否已完整，沒有證據。

「之後不會更正」已確認：
- daily_update.py:136-137 與 282-286：當 d_max 已等於目標日時回 None，記為「Daily store already current」。
- daily_refresh.py:150、166-169：已存在的日期直接略過。
- backend.log:45470-45477：9/23 21:40 那一輪沒有任何 MI_INDEX 請求。

finding 需要修正的地方：
- (1) 「最多約 4%（2454）」錯了。自選股中 6196 在 9/17 少了 16.95%；3034、2379、3545 也受影響，finding 沒列出。非自選的 2330 五個當日寫入日都少，9/18 少 9.05%。全市場個別個股可到 −99%。
- (2) 不只分母偏小。當日量本身（分子）也偏低，所以當天的 vol_ratio_5d 偏低，之後幾天因分母偏小而偏高。實例：2330 在 9/18 本地是 1.9317，官方量算出 2.1100。signal_volume_surge 的門檻是 ≥2.0（pipeline.py:659），所以本地 enriched 的 false 應該是 true，爆量訊號漏發了。
- (3) 2454 在 9/23 用官方量算出的量比是 0.6860，不是 0.6855。0.6855 是把本地的 9/23 量和官方前 5 日均量混在一起算出來的。6196 的 1.4324 對 1.3946 正確。

嚴重度維持 P2。這是資料時點過早、之後不更正的功能錯誤，口徑定義本身正確，不算 P1 的口徑錯誤。

另外發現一個原記錄沒有的面向，成因相同（分區已存在就不重抓）：9/16 和 9/23 的 taiwan/daily 與 enriched 完全沒有上櫃（TPEX）資料，只有上市 1,330 列。
- backend.log:37258 記錄 9/23 16:30:05 上櫃抓取失敗（peer closed connection），但 daily 狀態仍是 success（37265）。
- 21:40 那一輪也沒有重試。
- 使用者的自選 9 檔都是上市，所以自選不受影響；全市場選股與策略會受影響。建議另外立案，嚴重度推測 P1 到 P2。
    - 證據：
      - 腳本 AUDIT_SCRATCH/work/verify-dvol/cmp.py（本地 taiwan/daily 對官方 STOCK_DAY，10 檔 × 38 日）。輸出：380 列中 16 列有差、收盤價 0 差。差異明細 2330：9/16 −44,000、9/17 −597,000、9/18 −3,702,177（−9.05%）、9/22 −1,118,000、9/23 −839,000。2454：−249,000／−19,000／−434,469（−4.19%）／−68,000／−5,000。3034：9/17 −33,000、9/18 −29,388。6196：9/17 −165,000（−16.95%）、9/18 −3,000。2379：9/18 −32,000。3545：9/18 −6,000。0050、00878、00885、00893 全部 0 差。
      - 分區 part.parquet 的 mtime：9/16 16:30:22、9/17 16:19:15、9/18 17:03:37、9/22 16:30:03、9/23 16:30:05，這幾個有差。8/03～9/10 在 9/16 11:21:27 寫入，9/11、9/14、9/15 在 9/16 11:21:37～40 寫入，9/21 在 9/22 10:55:40 寫入，這些全部 0 差。
      - 腳本 verify-dvol\mi0923.py（本地 9/23 分區對參考資料 raw/twse_mi_index_20260923.json，該檔於 2026-09-24T01:53:02 抓取，端點與程式相同，都是 MI_INDEX）。1,330 列中 23 列量偏低、0 列偏高、收盤價 0 差。2454：本地 6,425,491，MI_INDEX 6,430,491。2330：本地 21,978,873，MI_INDEX 22,817,873。6464：本地 2,300，MI_INDEX 302,300。差額反推的成交價在當日高低之外：1109 12.15（最低 13.35）、2882 123.0（最高 112.5）、2454 5018.13（最低 5140）。MI_INDEX 附註：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - 腳本 verify-dvol\ratio.py：enriched 裡的 volume 與 taiwan/daily 完全相等（True）。9/23 的 vol_ratio_5d：2454 本地 0.6969，用官方量重算 0.6860（finding 寫 0.6855）；6196 本地 1.4324，官方 1.3946。9/17 6196 本地 0.5723，官方 0.6891（−16.95%）。9/18 2330 本地 1.9317，官方 2.1100。vol_ma5 最大偏差：2330 −5.22%（9/23）、6196 −2.69%（9/21）、2454 −1.96%（9/18）。
      - enriched date=2026-09-18 的 2330.TWSE：vol_ratio_5d=1.931707、signal_volume_surge=false。門檻見 backend/app/indicators/pipeline.py:659 `(pl.col("vol_ratio_5d") >= 2.0)`；用官方量算出 2.11，應為 true。
      - backend/app/indicators/pipeline.py:436、490：vol_ratio_5d = volume / volume.shift(1).rolling_mean(5)，分子是當日量，分母是前 5 日均量。
      - backend/app/taiwan/providers/snapshot_provider.py:33、129：上市日K取自 MI_INDEX（type=ALL）。
      - backend/app/taiwan/daily_update.py:136-137：earliest_available >= target_latest 時回 None。daily_update.py:282-286：d_range 為空就記「Daily store already current」。backend/app/taiwan/daily_refresh.py:150、166-169：已存在的日期 dates_skipped 後 continue。
      - backend/app/jobs/daily_pipeline.py:180：16:30 的 CronTrigger。daily_pipeline.py:195-196 註解：「已落盤的日期會被 refresh 略過, 不重複下載」。
      - data/backend.log:37256：2026-09-23 16:30:02 GET MI_INDEX?date=20260923。backend.log:45470-45477：9/23 21:40 那一輪只有 share capital 請求，沒有 MI_INDEX／dailyQuotes 請求，結果 daily=success、is_fully_current=True。
      - Yahoo（curl https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?interval=1d&range=1mo，2026-09-24 05:40 抓取）：meta.regularMarketVolume=6425491，與 finding 相符。但同一回應中 9/23 日線 volume=5,917,267，與本地和官方都不同，可見 Yahoo 的日線量是另一種口徑，只能當旁證。
      - 新面向（成因相同）：taiwan/daily 的 9/16 與 9/23 分區只有 TWSE 1,330 列、TPEX 0 列，enriched 9/16 與 9/23 也同樣 TPEX 0 列（9/15、9/17、9/22 各約 985～988 列）。backend.log:37258 記錄 2026-09-23 16:30:05「TPEx snapshot fetch failed for 2026-09-23: peer closed connection…」，backend.log:37265 記錄 daily=success，21:40 那一輪沒有重試。9/16 所在的那段 log 已輪替，查不到。
      - 鉅額交易的申報截止時間沒能上網查證：WebSearch 回報 session 額度 200/200 已用完；zh.wikipedia 的「臺灣證券交易所」條目只寫「盤後定價、零股及鉅額交易等，則另訂交易時間」。

### [P3 → P3] Yahoo 備援把當日最高價當成開盤價（確認成立）

- **主張**：yahoo_provider 設定 open=meta.regularMarketDayHigh（註解寫 approximated）。chart meta 本來就沒有開盤價欄位，但 indicators.quote[0].open 有正確值。
- **正確的定義或數值**：open=5220
- **app 實際行為**：open=5265（+0.86%）
- **對使用者的影響**：只在 MIS 缺某檔、改走 Yahoo 時出現；自選清單與個股頁顯示錯誤的開盤價（不會寫入日K，因為 hybrid 閘門會擋掉 Yahoo）。
- **修法建議**：改讀 result[0].indicators.quote[0].open[-1]，取不到就填 None，不要用最高價代替。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/yahoo_provider.py:126
- **線上證據**：
  - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?interval=1d&range=1d：meta 沒有 regularMarketOpen；regularMarketDayHigh 5265.0；indicators.quote[0].open [5220.0]
- **本地計算證據**：
  - 官方 9/23 2454 開盤 5220
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：yahoo_provider.py:126 把 meta.regularMarketDayHigh 當成開盤價，所以走 Yahoo 備援時，open 一定等於當日最高價。chart meta 沒有開盤價欄位，但 indicators.quote[0].open 有正確值（float32 精度，需要四捨五入）。以 9/23 實測：2454 顯示 5265，官方 5220（+0.86%）；自選各檔高估 0.17%～3.72%，只有開盤等於最高的日子（例如 3034）才碰巧正確。錯值只出現在 Monitor 報價面板的「開盤價」與個股頁的「今開」；自選清單要使用者自己打開預設隱藏的「開盤」欄位才看得到。hybrid 的 is_realtime 閘門會擋掉 Yahoo，所以錯值不會進入日K、即時K棒或 enriched 指標。
    - 理由：我自己重新抓了資料並跑過一次，finding 說的數值完全重現。

1. 程式碼：yahoo_provider.py:126 寫的是 `open=meta.get("regularMarketDayHigh")  # approximated`，下一行 127 的 high 也讀同一個欄位，所以 open 和 high 一定相同。repo 裡沒有任何程式讀 regularMarketOpen 或 indicators.quote，也沒有測試固定這個行為。

2. 線上重現：2026-09-24 05:37（台北時間，收盤後，回應仍是 9/23 的交易日）用程式碼同一個網址（interval=1d&range=1d）抓了自選 9 檔加 2330，共 10 次請求，每次間隔 11 秒。10 檔的 meta 都沒有任何名稱含 "open" 的欄位。indicators.quote[0].open 的值與官方開盤價一致，只差 float32 誤差（例如 0050 是 112.80000305175781）。

3. 用真實程式碼重現：把原始 JSON 當 mock_response_json 餵給 YahooRealtimeProvider.fetch_single_quote（純計算），得到的 open 都等於官方最高價：
- 2454：5265，官方開盤 5220，差 +45（+0.862%），與 finding 的 +0.86% 相符。
- 2379：+26（+3.385%）。6196：+20（+3.717%）。3545：+0.7（+1.346%）。00893：+0.51（+1.112%）。2330：+30（+1.212%）。0050：+0.35（+0.31%）。00878：+0.06（+0.17%）。00885：+0.04（+0.219%）。
- 3034：差 0，因為 9/23 官方開盤剛好等於最高，都是 561。

4. 官方與本地資料：TWSE STOCK_DAY、MI_INDEX 9/23、MIS 快照（o=5220、h=5265），以及本地 data/taiwan/daily/date=2026-09-23，都寫 2454 開盤 5220、最高 5265。

5. 影響範圍：
- 「不會寫入日K」成立。TaiwanHybridProvider._live_quote_frame（hybrid_provider.py:178）用 `not q.source_meta.is_realtime` 跳過 Yahoo 報價；Yahoo 報價寫死 is_realtime=False（yahoo_provider.py:110）。QuoteService._taiwan_realtime_records（quote_service.py:775）與 fetch_live_bar 都走這個閘門，所以 kline.py 的即時K棒注入與 enriched 資料都不會用到 Yahoo 的 open。分鐘K由成交價組成，不讀 quote.open。
- 會顯示錯值的地方：Monitor 頁 TaiwanQuotePanel.tsx:324 的「開盤價」，以及個股頁 TaiwanStockDetail.tsx:477 的「今開」。
- 自選清單會透過 watchlist_enrichment.py:240 傳出 open，但前端的「開盤」欄位預設隱藏（stock-info-fields.ts:31，visible:false），使用者自己打開才看得到。finding 說「自選清單顯示錯誤」略為誇大，應改成「自選清單若開啟開盤欄位才會看到」。
- 觸發條件：MIS 缺某檔時才會走 Yahoo。docs/code-review-2026-09-22.md:52 已記載，盤前試撮時段若 z 與 trade.z 同時缺，整批會落到 Yahoo，所以這條路徑實際走得到。

6. 修法補充：indicators.quote[0].open[-1] 是 float32 精度，建議取值後依升降單位四捨五入，或至少 round 到 2 位。

未驗證：盤中（交易時段）indicators.quote[0].open[-1] 是否總是有值。目前是收盤後，無法實測，所以「盤中也能取到正確開盤價」屬推測。

嚴重度維持 P3：只影響備援路徑的顯示值，不寫入資料、不進指標與訊號。
    - 證據：
      - backend/app/taiwan/realtime/yahoo_provider.py:126 `open=meta.get("regularMarketDayHigh"),  # approximated`；:127 high 讀同一欄位；:110 is_realtime=False
      - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?interval=1d&range=1d（2026-09-23T21:37:33Z 抓取）：meta.regularMarketDayHigh=5265.0，meta 沒有 regularMarketOpen（10 檔的 meta 都沒有名稱含 'pen' 的欄位），indicators.quote[0].open=[5220.0]；原始檔 AUDIT_SCRATCH/work/verify-yahoo-open/raw/2454.TW.json
      - 指令：cd backend && uv run python ...\verify-yahoo-open\compare.py（把原始 JSON 當 mock 餵給 YahooRealtimeProvider.fetch_single_quote）→ 2454 provider.open=5265.0，官方開盤 5220.0，差 +45（+0.862%）；2379 +26（+3.385%）；6196 +20（+3.717%）；3545 +0.7（+1.346%）；00893 +0.51（+1.112%）；2330 +30（+1.212%）；0050 +0.35（+0.31%）；00878 +0.06（+0.17%）；00885 +0.04（+0.219%）；3034 差 0（當日開盤=最高=561）
      - 官方參考資料 reference\csv\twse_mi_index_20260923_quotes.csv：2454 open_twd=5220.0、high_twd=5265.0；twse_stock_day_202608_202609.csv 2454 2026-09-23 open 5220.0 high 5265.0；mis_snapshot.csv tse_2454 o=5220.0 h=5265.0
      - 本地 parquet data/taiwan/daily/date=2026-09-23\part.parquet：2454.TWSE open 5220.0 high 5265.0（其餘 9 檔也都與官方一致）；指令 ...\verify-yahoo-open\local.py
      - backend/app/taiwan/providers/hybrid_provider.py:178 `if q.last_price is None or not q.source_meta.is_realtime: continue`（Yahoo 被擋在 live frame 外）；backend/app/services/quote_service.py:775 與 fetch_live_bar 都經過此閘門
      - 顯示點：frontend/src/components/monitor/TaiwanQuotePanel.tsx:324-325（開盤價）；frontend/src/pages/TaiwanStockDetail.tsx:477（今開）；backend/app/taiwan/watchlist_enrichment.py:240 傳出 open，但 frontend/src/lib/stock-info-fields.ts:31 的開盤欄位預設 visible:false
      - Yahoo 路徑實際走得到：docs/code-review-2026-09-22.md:52 盤前試撮時段若 z 與 trade.z 同時缺，整批會落到 Yahoo
      - 未驗證（推測）：盤中 indicators.quote[0].open[-1] 是否一定有值——驗證時是收盤後，無法實測

### [P3 → P3] 時段模型把 13:25–13:30 收盤集合競價當成連續交易（確認成立）

- **主張**：calendar 把 09:00–13:30 整段標為 OPEN，docstring 寫成「Continuous regular session」。_is_continuous_trading 據此在 13:25–13:30 放行告警，這段 MIS 報價也標為 realtime，但這段期間沒有任何成交。
- **正確的定義或數值**：13:25–13:30 應另設 closing_auction 狀態：不觸發告警，報價標為非成交價。
- **app 實際行為**：與連續交易時段視為同一種狀態。
- **對使用者的影響**：使用者目前有 0 條監控規則，不受影響；一旦啟用規則，這 5 分鐘可能依非成交價觸發告警。
- **修法建議**：MarketStatus 新增 CLOSING_AUCTION；_is_continuous_trading 與 MIS 的 is_realtime 在這段時間回 False，並修正 docstring。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/calendar.py:6-11、116-119
  - backend/app/services/quote_service.py:1247-1258（docstring 寫明 alerts must not fire on indicative or stale prices）
- **線上證據**：
  - https://support.fugle.tw/trading/trading-dictionary/9648/：「逐筆交易時間：整股盤中時段 9:00-13:25」「整股盤中收盤 13:25-13:30」
- **本地計算證據**：
  - verify_mis.py：13:25:00 與 13:29:59 都判定為 open
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：calendar.py 把 09:00–13:30 整段標為 OPEN，docstring 寫成「Continuous regular session」，沒有收盤集合競價（13:25–13:30）狀態。因此 _is_continuous_trading 在 13:25:00–13:30:00 都回傳 True，監控評估不會被擋。MIS 報價的時間在這段期間持續更新，被標為 status=realtime、is_realtime=True：9/18 實際錄到 19 根 realtime 分鐘 bar。這段期間沒有成交：進入時段後除第一根的跨界累積量外，量全部是 0，每檔只有一個凍結價格。顯示的價格推測是 13:25 前最後一筆逐筆成交價（trade.z），是已過時、目前無法成交的價格，不一定是試撮揭示價；它與官方收盤價在 9/18 有 7/9 檔不同，例如 2454 是 4730 vs 4710。啟用規則後，這 5 分鐘會用這個過時價格評估並可能觸發告警，違反 docstring 的設計意圖。使用者目前 0 條規則，不受影響。
    - 理由：finding 的核心敘述全部重現，說法也正確。程式、真實分鐘資料與線上來源三方面都吻合：

(1) 程式碼：calendar 在 t <= 13:30 時一律回傳 OPEN，docstring 與 MarketStatus 的註解都寫成「Continuous regular session／Continuous regular trading」，全 repo 找不到任何處理 13:25 或收盤集合競價的程式。

(2) 用純函式加上替換過的時鐘重現：13:25:00、13:27:30、13:29:59 三個時間點，get_market_status 都回傳 open，QuoteService._is_continuous_trading 回傳 True，_market_phase 回傳 regular；13:30:01 才轉成 post_close／False。MIS provider 收到報價時間 13:27:25 的 mock 快照，標為 status=realtime、is_realtime=True、is_stale=False。

(3) 真實資料：MinuteBarRecorder 只收 status=="realtime" 的列，bar 的時間取自 MIS 報價時間。9/18 的分鐘 parquet 在 13:25–13:28 有 19 根 bar，涵蓋 9 檔全部，證明集合競價期間 MIS 報價的時間持續更新，而且被當成即時報價放行。其中量大於 0 的只有各檔進入時段後的第一根，共 8 根、505 張。這幾根的前一根都在 13:25 之前，量是跨越 13:25 界線的累積量差。其後 11 根的量全部是 0。每檔在整個時段內只有 1 個價格。這些都符合交易規則的「13:25–13:30 累積委託，13:30 一次撮合」。

(4) 與官方收盤比對：這個時段顯示的價格在 9/18 有 7/9 檔與官方收盤不同（2454：4730 vs 4710；00878：34.90 vs 34.83；3034：555 vs 558），9/23 有 4/9 檔不同（2454：5180 vs 5185；3034：543 vs 545）。因此只要這 5 分鐘內有規則，就會拿一個已凍結、跟最終收盤不同的價格評估。

需要補充的細節：finding 在 expected／user_impact 把這段價格稱為「非成交價」。資料顯示的是凍結不動的單一價格，而且與 13:25 前最後一根 bar 的延續一致。推測這是 trade.z 帶出的最後一筆逐筆成交價，也就是已過時、目前無法成交的價格，而不是試撮揭示價。這點無法在本地證實：參考資料只有盤後的 MIS 快照，也不得連 MIS。

嚴重度：這個時段違反了 docstring 明寫的「alerts must not fire on indicative or stale prices」。但每天只有 5 分鐘，價格是數分鐘內的真實成交價，使用者目前也是 0 條規則，維持 P3 合理。

另外，9/23 在這個時段量為 0 不能當成獨立證據。backend.log 顯示後端 13:27:41 關閉、13:27:49 重新啟動，量的基準被重設。
    - 證據：
      - backend/app/taiwan/realtime/calendar.py:8 docstring「09:00 - 13:30: OPEN (Continuous regular session, 270 minutes, no lunch break)」；:23 REGULAR_OPEN_END = dt_time(13, 30)；:116-119 `elif t <= REGULAR_OPEN_END: return MarketStatus.OPEN`
      - backend/app/taiwan/realtime/models.py:35 `OPEN = "open"  # 09:00 - 13:30 (Continuous regular trading, verified)`，沒有收盤集合競價狀態
      - backend/app/services/quote_service.py:1247-1258 _is_continuous_trading 的 docstring 寫「alerts must not fire on indicative or stale prices」，實作是 `return get_market_status() == MarketStatus.OPEN`；:1275 _evaluate_monitors 只靠它擋告警
      - backend/app/taiwan/realtime/mis_provider.py:209-212 在 OPEN 時 status=REALTIME；:238 `is_realtime=(current_market_status == MarketStatus.OPEN and not is_stale)`
      - grep '13:25|closing.auction|集合競價|call auction' app/**/*.py：沒有任何收盤集合競價的處理
      - 本地重現（指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-closeauction/repro.py，輸出在 repro.out）：13:24:59／13:25:00／13:27:30／13:29:59／13:30:00 → get_market_status=open、_is_continuous_trading=True、_market_phase=regular；13:30:01 → post_close／False／close_final。finding 的「13:25:00 與 13:29:59 皆為 open」完全重現
      - repro.py Part 2：MIS mock 快照（t=13:27:25，於 13:27:30 取得）→ 2454.TWSE status=realtime、is_realtime=True、is_stale=False
      - backend/app/services/quote_service.py:1535 分鐘 bar 只收 `r.get("status") == "realtime"`；minute_recorder.py:81 與 :123 只在 OPEN 且報價時間落在 09:00–13:30 時才寫入
      - 真實資料 data/kline_minute/date=2026-09-18\part.parquet（flat_bars.py／repro.py）：13:25–13:28 共 19 根 realtime bar（13:25=2、13:26=9、13:27=6、13:28=2）；量大於 0 的只有各檔進入時段後的第一根，共 8 根、合計 505,000 股（262,000+243,000），前一根都在 13:25 之前；其後 11 根的量都是 0；每檔在時段內只有 1 個價格，19 根的 OHLC 全部相同
      - 官方收盤（reference\csv\twse_stock_day_202608_202609.csv）與時段內 realtime 價格比對：9/18 有 7/9 檔不同，例如 2454 4730 vs 4710、00878 34.90 vs 34.83、3034 555 vs 558、2379 726 vs 727、6196 539 vs 540；9/23 有 4/9 檔不同：2454 5180 vs 5185、3034 543 vs 545、2379 759 vs 761、00893 46.21 vs 46.19
      - data/backend.log：2026-09-23 13:27:41 行情服务已停止，13:27:49 重新啟動，因此 9/23 時段內量為 0 是基準重設造成，不列為證據；backend.log.1 顯示 9/18 13:25–13:30 每約 7 秒輪詢一次，沒有中斷
      - https://support.fugle.tw/trading/trading-dictionary/9648/：逐筆交易「交易時間為整股盤中的 9:00-13:25」，「整股收盤集合競價發生在 13:25-13:30」，集合競價「為累積一段時間之委託」後統一撮合
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所（WebFetch 摘要）：9:00–13:25 逐筆交易，13:25–13:30 收盤集合競價；原文「撮合成交時間為9:00至13:30，委託時間8:30至13:30」

### [P2 → P2] 盤中量能口徑與歷史口徑不一致：量比與週轉率在盤中系統性偏低 4.8%–8.2%（部分成立）

- **主張**：盤中今日列的 volume 來自 MIS v（整股一般交易，不含零股、盤後定價、鉅額），vol_ratio_5d 的分母和收盤後的今日列卻是官方日成交股數，而官方口徑包含零股、盤後定價與鉅額。兩種口徑被放進同一個比值，週轉率在 16:30 還會跳一次。
- **正確的定義或數值**：分子與分母同口徑，例如盤中只和前 5 日的一般交易量比，或盤中把可得的零股、盤後量一併計入。
- **app 實際行為**：分子只有一般交易，分母是全口徑。
- **對使用者的影響**：盤中卡片的量比與週轉率偏低，量比門檻型策略的判斷偏向保守；收盤後數值會跳動。
- **修法建議**：歷史量另外保存一般交易的口徑，或在盤中量比註明口徑並用同口徑分母；週轉率在盤中標示為估算值。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/mis_provider.py:165-167
  - backend/app/indicators/pipeline.py:1575-1587
  - backend/app/services/quote_service.py:177-223（週轉率＝volume×100/已發行股數）
  - backend/app/taiwan/watchlist_enrichment.py:81-97
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW：總量 5,909 張；「註：成交金額不含盤後定價、零股、鉅額、拍賣及標購」
- **本地計算證據**：
  - 官方 STOCK_DAY 與 MI_INDEX 註記：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
  - verify_volume.py（9/23 收盤）：MIS 口徑 vs 官方口徑依序為 2454 5,902,000 vs 6,430,491（−8.22%）、6196 1,657,000 vs 1,773,924（−6.59%）、0050 55,272,000 vs 58,059,253（−4.80%）、00878 24,973,000 vs 26,437,407（−5.54%）
  - 同分母下的量比：2454 0.6296 vs 0.6860、6196 1.3027 vs 1.3946、0050 0.6691 vs 0.7029、00878 0.9294 vs 0.9839
  - 週轉率：2454 收盤前的 MIS 口徑約 0.368%，16:30 後變成 0.400%
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：盤中今日列的 volume 來自 MIS 的 v，只含整股一般交易。vol_ratio_5d 的分母（repository.py:925，前 5 日合計）與 16:30 後的今日列，則是含零股、盤後定價、鉅額的官方日成交股數。因此盤中的 kline_daily_enriched 今日列量比系統性偏低：13:30 到 16:30 之間落差等於口徑差，9/23 為 4.8%–8.2%；16:30 改用官方量後回升。這會影響監控規則、signal_volume_surge（≥2.0）與盤中執行的策略，但不影響台股自選卡片，因為卡片的量比恆為「—」。週轉率方面，enriched 系畫面（StockInfoBar、K 線今日列）在 16:30 從 MIS 口徑跳到官方口徑（2454 從 0.368% 變成 0.400%）。自選卡片的換手率則一直用 MIS 報價量，收盤後到午夜都停在偏低值，因此這段時間卡片與資訊列對同一天的數字不一致。
    - 理由：核心主張成立：盤中量比的分子只算整股一般交易，分母卻是含零股、盤後定價、鉅額的日成交股數，兩者口徑不同；週轉率也有同樣的口徑落差。9/23 的落差數字我用參考資料重算，結果與 finding 一致。但 finding 對使用者影響的描述有兩處不正確，所以判部分成立。

一、機制確認
(1) 分子：mis_provider.py 把 MIS 的 v（張）乘以 1000 當成當日成交量（第 165-167 行）。盤中輪詢把這筆量寫成今日列，送進增量計算（quote_service.py:863、883、1664-1688）。量比在 pipeline.py:1575-1587 算成 volume×(1/session_progress)/(_vol_ma5_prev_sum/5)。session_progress 只涵蓋 9:00–13:30 的一般交易時段（calendar.py:124-146）。
(2) 分母：_vol_ma5_prev_sum 是歷史 volume 最後 5 筆的合計（repository.py:925）。歷史 volume 來自官方日成交股數，已實際讀檔確認：本地 2454 在 9/22 為 12,063,543 股，不是 1000 的倍數，表示含零股。
(3) MIS 的 v 不含零股與盤後定價，參考資料的 MIS 快照可證：2454 系統時間已是 14:30（盤後定價結束），v 仍是 5902 張，與 13:30 收盤一致；官方同日為 6,430,491 股，尾數 491 股只可能來自零股。官方 STOCK_DAY 與 MI_INDEX 的附註也寫明「含一般、零股、盤後定價、鉅額交易」。
(4) 定義面：MBA 智庫的量比定義是「現成交總手 ÷（過去 5 日平均每分鐘成交量 × 當日累計開市分鐘數）」，分子與分母必須是同一段交易時間內的量。維基百科說明一般交易為 9:00–13:30，盤後定價、零股、鉅額另有交易時段。所以用全口徑日量當分母，會高估每分鐘基準、讓量比偏低。CONTRIBUTING.md 第 4 節也禁止「靜默換用錯誤口徑」。
(5) 落差重算：2454 −8.22%、6196 −6.59%、0050 −4.80%、00878 −5.54%，與 finding 相同。13:30 到 16:30 之間 session_progress=1，量比直接偏低相同比例；16:30 的日更新（daily_pipeline.py:177-190）改用官方量後，今日列才變成 0.6969（2454，本地實存值）。
(6) Taiwan 的 ETF 在本部署也走同一條增量路徑：instruments_etf 與 kline_etf_* 都是空的，ETF 被歸入 stock_records，所以 4 檔的推論都適用。

二、finding 說錯或說得不精確的地方
(a) 「盤中卡片的量比偏低」不成立。台股自選列的欄位清單沒有 vol_ratio_5d（watchlist_enrichment.py:42-44），api/watchlist.py:458-464 把缺的欄位補成 None，前端 Watchlist.tsx:591 對台股一律顯示「—」。盤中量比的口徑誤差實際只影響 kline_daily_enriched 的今日列，也就是監控規則（使用者 0 條）、signal_volume_surge（量比 ≥2.0）、盤中執行的策略與通用表格。
(b) 「週轉率在 16:30 還會跳一次」只對 enriched 系的畫面成立，例如 StockInfoBar 與 K 線 API 的今日列：盤中由 _fill_taiwan_turnover_rate 用 MIS 量填入，16:30 後改成官方量，2454 從約 0.368% 變成 0.400%。自選卡片的換手率是用即時報價的 q.volume 現算（watchlist_enrichment.py:81-97），而 MIS 當晚仍回傳 9/23 的 v=5902 張（快照取得時間為 9/24 01:49），所以卡片在 16:30 不會跳動，會一直停在 0.368%。結果是 16:30 到午夜之間，卡片與資訊列對同一天的數字不一致。
(c) finding 的「同分母量比」（2454 0.6296 vs 0.6860）用的是 STOCK_DAY 的分母，不是 App 實際存的分母。依本地實存分母，2454 為 0.6401 vs 0.6969，6196 為 1.3380 vs 1.4324。相對落差的結論不變。
(d) 「4.8%–8.2%」只是 9/23 單日 4 檔的觀察。偏低方向是系統性的，因為官方量一定大於等於一般交易量，但幅度會隨標的與日期變動。

三、嚴重度
維持 P2。依 CONTRIBUTING 第 11 節，口徑錯誤可以算 P1，但這個問題只在盤中出現，幅度約 5–8%，16:30 後自動修正（enriched 系）。在使用者情境下，量比並沒有顯示在自選卡片上，監控規則也是 0 條。實際看得到的影響是：卡片換手率少算約 5–8%，而且收盤後持續到午夜；以及量比 2.0 門檻附近的盤中訊號可能漏觸發。

附帶觀察（不在本 finding 範圍）：本地 taiwan/daily 與官方 STOCK_DAY 相比，380 列中有 16 列量偏少，全部集中在 9/16–9/23，官方減本地一律為正（最小 3,000 股），例如 2454 在 9/16 差 249,000 股，2330 在 9/18 差 3,702,177 股。推測原因是 16:30 抓取之後官方又有鉅額交易，而 21:40 那班會略過已落盤的日期（daily_pipeline.py:194-195），所以沒有更新。此點未完整驗證，建議另案稽核。
    - 證據：
      - backend/app/taiwan/realtime/mis_provider.py:165-167 — v_lots = item['v']; volume_shares = int(round(v_lots * 1000))
      - backend/app/indicators/pipeline.py:1575-1587 — vol_ma5_prev = _vol_ma5_prev_sum / 5; vol_ratio_5d = volume * (1/session_progress) / vol_ma5_prev
      - backend/app/repository.py:925 — _vol_ma5_prev_sum = volume.tail(5).sum()（歷史＝官方日量）
      - backend/app/services/quote_service.py:863,883,1664-1688 — 盤中把 MIS records 寫成今日列並做增量 enriched；177-223 _fill_taiwan_turnover_rate 用今日列的 volume 算換手率
      - backend/app/taiwan/realtime/calendar.py:124-146 — session_progress 只算一般交易時段（9:00–13:30）
      - backend/app/taiwan/watchlist_enrichment.py:42-44（台股列沒有 vol_ratio_5d）、81-97（換手率＝q.volume×100/issued_shares）；backend\app\api\watchlist.py:458-464 把缺的欄位補 None；frontend\src\pages\Watchlist.tsx:591 量比顯示「—」
      - backend/app/jobs/daily_pipeline.py:177-190 — 16:30 台股日更新，接著 refresh_enriched_cache；194-195 — 21:40 那班會略過已落盤的日期
      - 參考資料 raw/mis_snapshot.json：2454 '%':'14:30:00'、tlong 13:30、v='5902'（張）；0050 v=55272；00878 v=24973；6196 v=1657（於 2026-09-24 01:49 取得，仍是 9/23 資料）
      - 參考資料 raw/twse_stock_day_2454_202609.json 的 notes：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」；MI_INDEX 9/23 notes：「本統計資訊含一般、零股、盤後定價、鉅額交易」；2454 9/23 成交股數 6,430,491
      - 本地重算（uv run python ...\work\verify_volume_defn\ratios.py）：MIS 對官方的落差 2454 −8.22%、6196 −6.59%、0050 −4.80%、00878 −5.54%；換手率 2454 0.3677% vs 0.4007%、6196 0.7521% vs 0.8051%（issued_shares 取自 data\taiwan\share_capital.parquet：2454 為 1,604,952,613）
      - 本地實存（read_store.py）：kline_daily_enriched 9/23 的 2454 volume 6,425,491、vol_ratio_5d 0.696907、turnover_rate 0.400354；6196 為 1,773,924、1.432449、0.805123。改用 MIS 分子時 App 會得出 2454 0.6401、6196 1.3380
      - https://wiki.mbalib.com/wiki/量比 — 「量比＝现成交总手/〖(过去5个交易日平均每分钟成交量)×当日累计开市时间(分)〗」
      - https://zh.wikipedia.org/wiki/臺灣證券交易所 — 一般交易「撮合成交時間為9:00至13:30」；「盤後定價、零股及鉅額交易等，則另訂交易時間、委託及撮合方式」
      - 附帶（store_vs_official.py）：taiwan/daily 與 STOCK_DAY 比對 380 列，其中 16 列本地偏少，全部在 9/16–9/23，差額最小 3,000 股，例如 2454 在 9/16 差 249,000、2330 在 9/18 差 3,702,177
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：盤中今日列的 volume 取自 MIS v（整股一般交易，不含盤後定價、零股、鉅額）。量比分母則取自本地歷史，也就是 16:30 抓取的 MI_INDEX（含一般、零股、盤後定價、鉅額）。兩種口徑被放進同一個比值，導致盤中量比與週轉率一致偏低。

9/23 收盤當下的落差為 2454 −8.2%、6196 −6.6%、0050 −4.8%、00878 −5.5%。依 Yahoo 昨量對照，9/22 的落差為 −13.2%、−7.0%、−4.9%、−3.8%，所以幅度會逐日浮動，約在 4%–13%，不是固定的 4.8%–8.2%。

依 app 實際使用的分母，9/23 收盤當下量比為 2454 0.640（16:30 後變 0.697）、6196 1.338（→ 1.432）、0050 0.669（→ 0.703）、00878 0.929（→ 0.984）。2454 的週轉率 0.368% 在 enriched 今日列與 K 線於 16:30 後變成 0.400%。

受影響的是 K 線 API 注入的盤中即時 K 棒，以及讀取盤中 enriched 的策略與選股。自選卡片的台股列不顯示量比；它的週轉率一直用 MIS 量，16:30 後仍停在約 0.368%，與 enriched 不一致。

此外，本地歷史分母本身也不完全是全口徑：2454 與 6196 有多日低於事後的 STOCK_DAY，最多少 434,469 股，推測是 16:30 之後才申報的量沒被補回。
    - 理由：核心主張可以重現：盤中今日列的 volume 取自 MIS v（張×1000），不含盤後定價、零股、鉅額；量比分母與 16:30 之後的今日列則是 MI_INDEX 全口徑。9/23 收盤的量能落差 −8.22%／−6.59%／−4.80%／−5.54%，我重算後逐位相符。2454 週轉率 0.3677% 對 0.4004%，也相符。

但有四處要更正：
(1) finding 列出的 2454 與 6196 量比數字（0.6296/0.6860、1.3027/1.3946），分母是後來重抓的 STOCK_DAY，不是 app 實際使用的本地歷史。本地歷史的 2454 在 9/16、9/17、9/18、9/22、9/23 都比 STOCK_DAY 少（例如 9/18 少 434,469 股），6196 在 9/17、9/18 也偏少。我把資料餵進 production 純函式 compute_enriched_today 實跑，app 在收盤當下會顯示 2454 0.6401，16:30 後落盤為 0.6969；6196 則是 1.3380 → 1.4324。0050 與 00878 的數字與 finding 完全一致。兩種分母下，相對落差都一樣。
(2) 「4.8%–8.2%」只是單日樣本。Yahoo 的 9/23 總量剛好等於 MIS v 加上 fv（4/4 檔相符），因此可以把 Yahoo 的昨量當作同口徑代理。用它對照 9/22 官方量，落差為 2454 −13.2%、6196 −7.0%、0050 −4.9%、00878 −3.8%。方向一致偏低，幅度則逐日浮動，約 3.8%–13.2%。
(3) 「盤中卡片的量比」這個說法不精確。自選卡片的台股列根本不帶 vol_ratio_5d（enrich_taiwan_watchlist_rows 沒有這個欄位，watchlist.py 以 setdefault 補成 None）。受影響的是 K 線 API 注入的盤中即時 K 棒（kline.py:579-584），以及讀取盤中 enriched 的策略與選股。
(4) 「週轉率在 16:30 會跳一次」只發生在 enriched 的今日列與 K 線。自選卡片的週轉率一直用 MIS 的 q.volume，而且 get_quotes 在收盤後仍優先走 MIS，所以 16:30 之後卡片仍停在約 0.368%，與 enriched 的 0.400% 並存不一致（這一點是從程式碼推得，未實機觀察）。

嚴重度維持 P2：偏差有界，只影響盤中與收盤到 16:30 這段；使用者沒有監控規則，自選卡片也不顯示台股量比。

另外有一個新面向，不在本 finding 內：本地 taiwan/daily 的歷史量與 STOCK_DAY 有多日不一致。原因推測是 16:30 抓 MI_INDEX 時，晚申報的鉅額或更正尚未計入；而 21:40 那班重跑會略過已落盤的日期（daily_pipeline.py:194-195 的註解），所以不會補回。這代表「分母就是官方全口徑」這個前提本身也不完全成立。
    - 證據：
      - 程式碼：backend/app/taiwan/realtime/mis_provider.py:165-167，volume_shares = v×1000
      - 程式碼：backend/app/indicators/pipeline.py:1575-1587，vol_ratio_5d = volume×time_factor ÷ (_vol_ma5_prev_sum/5)
      - 程式碼：backend/app/repository.py:924-925，分母取歷史 enriched 的 volume.tail(5)；backend/app/services/quote_service.py:666-669、867-877，台股盤中把 MIS 紀錄 flush 成今日列，再走增量 enriched；backend/app/services/quote_service.py:177-223，以已發行股數補 turnover
      - 程式碼：backend/app/taiwan/watchlist_enrichment.py:81-97、268，自選卡片的週轉率用 q.volume（MIS）；backend/app/taiwan/realtime/service.py:115-122，get_quotes 一律先試 MIS；backend/app/api/watchlist.py:449-465，台股列沒有 vol_ratio_5d，以 setdefault 補 None；backend/app/api/kline.py:579-584，K 線即時 K 棒注入 vol_ratio_5d/turnover_rate
      - 程式碼：backend/app/jobs/daily_pipeline.py:177-180（16:30 班次）、194-195（重跑會略過已落盤日期）
      - 官方參考：reference/raw/twse_stock_day_2454_202609.json notes[2]，以及 twse_mi_index_20260923.json tables[8].notes[3]：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - 重算（work/verify-mispool/repro.py → repro_out.txt）：9/23 的 MIS v×1000 對 STOCK_DAY：2454 5,902,000/6,430,491 −8.219%、6196 1,657,000/1,773,924 −6.591%、0050 55,272,000/58,059,253 −4.801%、00878 24,973,000/26,437,407 −5.539%，與 finding 逐位相符
      - 重算：以 STOCK_DAY 9/16–9/22 當分母，量比 2454 0.6296/0.6860、6196 1.3027/1.3946、0050 0.6691/0.7029、00878 0.9294/0.9839，可重現 finding 數字；改用本地歷史當分母（即 app 實際用的），2454 0.6401/0.6974（落盤值 0.696907）、6196 1.3380/1.4324
      - 實跑 production 純函式（work/verify-mispool/run_pipeline.py → run_pipeline_out.txt），compute_enriched_today 在 session_progress=1.0 時，MIS 口徑為 2454 0.64013、6196 1.33803、0050 0.66913、00878 0.92940；官方口徑為 0.69745、1.43245、0.70287、0.98390
      - 本地與官方逐日比對：2454 在 9/16 −249,000、9/17 −19,000、9/18 −434,469、9/22 −68,000、9/23 −5,000（本地 taiwan/daily 減 STOCK_DAY）；6196 在 9/17 −165,000、9/18 −3,000；0050 與 00878 全部相同
      - 週轉率：已發行股數 1,604,952,613（本地 share_capital.parquet 與 openapi t187ap03_L 相同）；MIS 口徑 0.367737%，STOCK_DAY 0.400665%，enriched 9/23 落盤值 0.4003539
      - 線上來源 https://tw.stock.yahoo.com/quote/2454.TW（報價時間 2026/09/23 14:30）：總量 5,909 張、昨量 10,528 張，注記「成交金額不含盤後定價、零股、鉅額、拍賣及標購」。5,909 = MIS v 5,902 + fv 7；昨量 10,528 對官方 9/22 的 12,131,543 股，差 −13.2%
      - 線上來源 https://tw.stock.yahoo.com/quote/6196.TW：總量 1,658（= v 1,657 + fv 1）、昨量 1,135（對官方 1,220,757，差 −7.0%）；https://tw.stock.yahoo.com/quote/0050.TW：總量 55,346（= 55,272 + 74）、昨量 80,194（對官方 84,309,564，差 −4.9%）；https://tw.stock.yahoo.com/quote/00878.TW：總量 24,985（= 24,973 + 12）、昨量 23,821（對官方 24,760,011，差 −3.8%）
      - MIS 快照 reference/raw/mis_snapshot.json：時間 %=14:30:00、t=13:30:00，2454 的 v 仍為 5902，表示 v 不含 14:30 撮合的盤後定價；fv 欄位即為盤後定價量，這一點是推測，依據是 4/4 檔都符合 Yahoo 總量 − v = fv

### [P3 → P2] 分鐘K成交量歸屬錯位：Δ量整筆算給觀察到它的那一分鐘，空窗後的量集中在單一根（部分成立）

- **主張**：_fold 把兩次快照之間的累計量差整筆算進「本次快照 t 所在的分鐘」，金額用本次價格乘Δ量估算。輪詢空窗（備援或過期剔除）之後，整段期間的量會集中在下一根；13:25–13:30 沒有任何成交，這段的分鐘K卻出現量。這與 docstring 所說的「缺漏就留空」不一致。
- **正確的定義或數值**：量歸在成交所在的分鐘，或至少不讓集合競價的分鐘出現非 13:30 的量；空窗後的累計差應標記為跨段。
- **app 實際行為**：累計差整筆落在下一根，集合競價期間出現假量。
- **對使用者的影響**：分時量柱在空窗後出現尖峰，13:25–13:29 有假量；金額只是估算值卻沒有標示。影響僅限分鐘圖。
- **修法建議**：Δ量跨越超過 1 分鐘或跨越 13:25 時，標記為跨段或按時間比例分配；13:25–13:29:59 不歸入任何成交量；amount 欄位標示為估算。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/minute_recorder.py:132-150
  - backend/app/taiwan/realtime/minute_recorder.py:5-7（docstring）
- **線上證據**：
  - https://rich01.com/pricing-before-market-open/：「試搓期間並不會有買賣單成交」
- **本地計算證據**：
  - kline_minute 9/18 的 13:26 那根：2454 39,000 股、00878 102,000 股、6196 62,000 股、2379 17,000、00885 13,000、3545 10,000，這些量只可能是 13:25:00 之前成交的
  - 9/23 在 10:10:37–10:11:56 有備援空窗，之後 00893 10:11 那根為 38,000 股（前後幾根為 0–2,000 股）
  - 註：9/18 跑的是舊版程式碼，但 Δ量歸屬的邏輯在這之後沒有改動（B11 只修鎖）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：MinuteBarRecorder._fold（minute_recorder.py:134-150）把兩次 realtime 快照之間的累計量差整筆算進本次快照所在的那一分鐘，金額用本次價格乘Δ量估算。只要進程沒有重啟，任何個股的空窗（MIS 被剔除或過期、備援、斷網）期間的成交量都會擠進空窗後的第一根。進程若在空窗中重啟，這段量反而直接丟掉（首筆快照不計量）。同一種空窗，兩種處理方式不一致，也違反 docstring（:5-7）與 :65-67 的精神。

已由實際資料重現：
- 9/18 最嚴重：12:04 到 12:50 斷網 45 分鐘，進程沒有重啟。12:50/12:51 那一根：0050 為 15,167,000 股，占官方日量的 17.5%，是當日平均分鐘量的 64 倍；2379 為 540,000 股，占 21.0%；00893 為 111,000 股，占 18.4%；2454 為 1,246,000 股，占 12.0%。
- 9/18 收盤集合競價期間（依證交所制度 13:25 到 13:30 沒有成交）：13:25 與 13:26 那兩根分別出現 13:25 以前的成交量。13:26 那根是 2454 39,000、00878 102,000、6196 62,000、2379 17,000、00885 13,000、3545 10,000；13:25 那根是 0050 230,000、3034 32,000。

finding 舉的 9/23 00893 10:11（38,000 股）數值正確，但 10:11 前面沒有缺分鐘，不能證明是空窗造成的假量，不應當作證據。

影響不只分鐘圖：回測開啟 minute_fill 且沒有參考線時，engine.py:1603-1608 會用這些分鐘 K 的 sum(amount)/sum(volume) 算 VWAP 成交價。因此是已落檔的錯誤資料，並且流進回測，嚴重度 P2。

修正方向：
- 空窗後（兩次快照相隔超過 1 分鐘，或跨過 13:25）的Δ不要整筆算給單一分鐘；可以捨棄，或標記為跨段，和重啟時的處理一致。
- 13:25:00 到 13:29:59 的快照不計任何量。
- amount 要標示為估算值。
    - 理由：核心機制與 9/18 收盤段的數值都完全重現，但 finding 有三處需要修正：(1) 9/23 00893 那個例子證明不了「空窗導致尖峰」；(2) 它漏掉了真正嚴重的一次：9/18 斷網 45 分鐘；(3) 影響不只分鐘圖。

【程式碼】minute_recorder.py:134-150 用 cum − previous 算 Δ，整筆加到本次快照 t 所在的那一分鐘（:132、:149）；金額是本次價格乘 Δ（:150）。同一進程裡如果中途有空窗，_cum_volume 不會重設，空窗期間的量會全部落在下一根。docstring（:5-7）說缺漏的時段就留空；同檔 :65-67 也有規則：啟動後第一筆快照的累計量屬於沒看到的分鐘，所以不計量。中途空窗卻沒有照這條規則處理，兩者不一致。9/18 的資料由 96892bf 產生，9/23 的資料在 60df574（9/23 22:13）之前產生。我用 git diff 96892bf 60df574 比對，_fold 沒有改動，只多了 _write_lock。

【數值重現】kline_minute 的 datetime 以 UTC 儲存，我轉成台北時間後比對。
- 9/18 13:26：2454 39,000、00878 102,000、6196 62,000、2379 17,000、00885 13,000、3545 10,000，與 finding 完全一致。
- 同一機制也出現在 13:25：0050 230,000、3034 32,000。
- 13:26 到 13:28 的其餘各根全部是 0，代表集合競價期間累計量沒有變。
- log 顯示 13:23 到 13:30 約每 7 秒輪詢一次，沒有中斷，所以空窗是個股不在 realtime 狀態造成的。log 沒有逐檔紀錄。
- 9/23 00893：10:11 是 38,000，10:08 到 10:10 與 10:12 都是 0，10:13 是 1,000、10:14 是 2,000，與 finding 一致。但 10:10 已經有一根，10:11 前面沒有缺分鐘，所以錯置的量最多只是 10:10 尾段的成交；10:15 前面也沒有空窗，卻有 17,000。log 在 10:11:07 到 10:11:55 確實有 STOCK_DAY_ALL 備援，但沒有逐檔狀態。因此不能證明 38,000 是假量。

【finding 沒提到、影響更大的一次】9/18 12:04 起 DNS 失敗（12 點這一小時有 284 次 getaddrinfo failed），12:51:21 才恢復（該次刷新耗時 2,636,398ms）。11:20:05 到 13:01:13 之間進程沒有重啟，所以 12:50/12:51 那一根吃下了 45 到 48 分鐘的量：
- 0050：15,167,000 股，占官方日量 86,731,065 的 17.49%，是當日沒有空窗的分鐘 K 平均量的 64 倍。
- 2379：540,000，占 21.02%。
- 00893：111,000，占 18.44%。
- 3034：735,000，占 15.44%。
- 2454：1,246,000，占 12.02%。

全部 4 天合計：前面沒有空窗的 K 棒量比平均定為 1.0；前面空 1 分鐘的是 2.03、空 2 到 4 分鐘的是 2.91、空 5 分鐘以上的是 24.2。對照組：9/23 13:27:41 進程重啟後，13:27/13:28 那幾根雖然前面空了 108 分鐘，量卻是 0（基準被重設）。所以空窗之後的量，要看進程有沒有重啟：重啟就丟掉，沒重啟就擠進一根，處理方式不一致。

【金額】只有一筆快照的 K 棒，amount − close×volume 全部等於 0，確認金額是估算值。

【網路查證】zh.wikipedia〈臺灣證券交易所〉：「盤中 (09:00-13:25)」採「逐筆交易」，「收盤 (13:25-13:30)」採「集合競價」。rich01：「因為是模擬，所以在試搓期間並不會有買賣單成交」，並寫明「收盤前試搓時間：13:25~13:30」。官方參考資料的 MIS 快照也顯示 2454 的 trade.t 是 13:30:00。WebSearch 額度已用完，只用 WebFetch 抓了 3 頁。

【影響修正】finding 說影響只限分鐘圖，這不對：
- 回測開啟 minute_fill 時，engine.py:1674 用 get_minute_by_dates 讀 kline_minute。
- 沒有參考線時，engine.py:1603-1608 用 sum(amount)/sum(volume) 算 VWAP 成交價。
- minute_fill 由 api/backtest.py:345 對外開放。

所以擠在一根的量加上估算的金額，會影響那 4 天這 9 檔回測成交價的權重。幅度沒有量化（推測不大）。

【嚴重度】這些是已經落檔的錯誤資料，由網路失敗路徑產生（CONTRIBUTING 第 11 節第 8 點要求失敗路徑不得產生錯誤狀態），而且會流進回測的 VWAP，所以調高為 P2。沒有觸及金融口徑，不到 P1。

【附帶觀察，未深究】
- 9/18 2454 在 13:26 那根的價格是 4730，官方收盤是 4710，所以 13:25 到 13:29 那幾根的價格也不是成交價（推測是試撮價或最後的逐筆成交價）。
- 4 天都沒有 13:30 那一根（最後一根在 13:26 到 13:28），收盤集合競價的量沒有進入分鐘 K。推測原因是 calendar.py:118 的 t <= 13:30:00 帶秒比較。
    - 證據：
      - backend/app/taiwan/realtime/minute_recorder.py:132-150：minute = quote_dt 取整到分鐘；delta = cum - previous（同一進程不重設）；bar.volume += delta；amount += price * delta
      - backend/app/taiwan/realtime/minute_recorder.py:5-7 與 :65-67：docstring 說缺漏留空，並規定啟動後第一筆快照不計量；中途空窗卻沒有照同一規則處理
      - git diff 96892bf 60df574 -- minute_recorder.py：_fold 沒有改動，只加了 _write_lock
      - 腳本 AUDIT_SCRATCH/work/verify-minvol/minbars.py close：9/18 13:26 的 2454=39000、00878=102000、6196=62000、2379=17000、00885=13000、3545=10000（與 finding 相同）；0050 與 3034 在 13:25 分別是 230000、32000；13:26 到 13:28 其餘各根都是 0
      - backend.log.1 在 2026-09-18 13:23 到 13:30 約每 7 秒輪詢一次，沒有中斷（例如 13:24:51、13:25:01、13:25:08…13:29:54），13:30:06 是 close final sync
      - minbars.py sym 2026-09-23 00893.TWSE：10:08 到 10:10 為 0，10:11=38000，10:12=0，10:13=1000，10:14=2000，10:15=17000；gapstats 顯示 10:11 前面沒有缺分鐘（gap_before=0）
      - backend.log 2026-09-23：10:10:37 到 10:10:58 的行情刷新檔數從 8 降到 4；10:11:07 到 10:11:55 每次輪詢都呼叫 STOCK_DAY_ALL（備援）；沒有逐檔的 realtime 狀態紀錄
      - backend.log.1 2026-09-18：12:04:08 起 MIS timed out，接著大量 getaddrinfo failed（12 點這一小時共 284 次）；12:51:21「行情刷新: 6 只股票…耗时 2636398ms」；服務啟停紀錄顯示 11:20:05 到 13:01:13 之間沒有重啟
      - outage.py 輸出（verify-minvol\outage_out.txt），占日量的分母是官方 STOCK_DAY 9/18 日量（reference csv）：0050 在 12:50 為 15,167,000 股（gap 45 分），占 86,731,065 的 17.49%；2379 為 540,000，占 21.02%；00893 為 111,000，占 18.44%；3034（12:51）為 735,000，占 15.44%；2454 為 1,246,000，占 12.02%；以上各根的 amount − close×volume 都是 0
      - gapstats.py（verify-minvol\gapstats_out.txt），K 棒量除以同檔同日沒有空窗的 K 棒平均量：前面沒有缺分鐘 n=2602 均值 1.0；缺 1 分鐘 n=842 均值 2.03；缺 2 到 4 分鐘 n=240 均值 2.91；缺 5 分鐘以上 n=24 均值 24.2
      - 對照組：9/23 在 13:27:41 關機、13:27:49 重啟後，13:27/13:28 那幾根雖然前面空了 108 到 109 分鐘，量都是 0，因為基準被重設
      - https://zh.wikipedia.org/wiki/臺灣證券交易所：「盤中 (09:00-13:25)」「原則上採逐筆交易」；「收盤 (13:25-13:30)」採「集合競價」
      - https://rich01.com/pricing-before-market-open/：「因為是模擬，所以在試搓期間並不會有買賣單成交」；「收盤前試搓時間：13:25~13:30」
      - reference raw/mis_snapshot.json：2454 的 trade={'t':'13:30:00','v':560,...}，集合競價在 13:30:00 撮合一次
      - backend/app/backtest/engine.py:1603-1608（VWAP = sum(amount)/sum(volume)）與 :1674（repo.get_minute_by_dates 讀 kline_minute）；backend/app/api/backtest.py:345 對外開放 minute_fill
      - 附帶：9/18 2454 在 13:26 的價格是 4730，官方收盤是 4710（reference STOCK_DAY）；4 天都沒有 13:30 那一根，最後一根在 13:26 到 13:28

## 確認正確的項目

- **MIS z→last_price（含 trade.z 備援）**：用 mock_response_json 把 raw/mis_snapshot.json 餵給 TwseMisRealtimeProvider.fetch_quotes（並 monkeypatch taipei_now），再和官方 9/23 收盤價比對。邏輯在 mis_provider.py:150-159
  - 來源：本地：verify_mis.py 輸出，2454 5185=5185、6196 549=549、0050 112.45=112.45、00878 35.09=35.09、6488 950=950（TPEx 9/23）
  - 來源：http://kentyeh.blogspot.com/2015/07/blog-post_16.html：z「最近或最後成交價」
  - 來源：https://blog.hungwin.com.tw/csharp-twse-price/：z 偶爾為 "-"，因為「證交所即時價格是每 5 秒更新一次」
- **MIS y→prev_close（非除權息日）**：mis_provider.py:160。5 檔的 y 與官方 9/22 收盤價比對（MI_INDEX 收盤價減去漲跌價差）
  - 來源：本地：2454 5180、6196 535、0050 111.85、00878 35.21、6488 971，全部一致
  - 來源：https://zys-notes.blogspot.com/2020/01/api.html："y" - "昨收"
- **MIS o/h/l→開、高、低**：mis_provider.py:161-163，和官方 9/23 開高低比對
  - 來源：本地：5/5 一致（例如 2454 5220/5265/5140）
- **即時 change／change_pct 公式與單位**：mis_provider.py:169-174 算出百分數，quote_service.py:778-783 再除以 100 轉成小數。用 (z−y)/y×100 獨立計算比對
  - 來源：本地：2454 0.0965、6196 2.6168、0050 0.5364、00878 -0.3408、6488 -2.1627，app 與獨立計算完全相同
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW：漲跌 5.00、漲跌幅 0.10%
- **五檔 a/b/f/g（張→股）**：mis_provider.py:62-77、140-143。最佳一檔與 MI_INDEX 的最後揭示買賣價量比對
  - 來源：本地：2454 買 5180×20 張→20000 股、賣 5185×14 張→14000 股；0050 112.45×246／112.5×163；00878 35.09×58／35.10×323；6196 548×6／549×1，全部一致
  - 來源：https://zys-notes.blogspot.com/2020/01/api.html："b"「揭示買價(從高到低，以_分隔資料)」、"g"「揭示買量」
- **MIS v 單位（張×1000）的數值換算**：mis_provider.py:165-167。換算本身正確，口徑問題另列 finding
  - 來源：本地：2454 v=5902 張→5,902,000 股
  - 來源：MI_INDEX 註記：「除境外指數股票型基金及外國股票第二上市外，餘交易單位皆為千股」（raw/twse_mi_index_20260923.json）
- **MIS u/w（漲跌停價）**：app 不讀 u/w，而是用 pipeline.py:1967-2010 的 add_limit_distance_columns 自算。把 ref 當作 prev_close 與 close 餵入，再反推漲跌停價；另外依檔位規則獨立計算一次
  - 來源：本地 verify_limits.py：MIS 9/23 五檔 u/w 全部一致（例如 2454 5695/4665）；TWT49U 7 筆除權息日的漲跌停價全部一致（例如 00878 8/18 參考價 32.74→36.01/29.47；6196 6/24 參考價 534.55→588/481.5）
- **t/d→quote_time；tlong 不使用**：mis_provider.py:177-191 用 d+t。快照中 t=13:30:00，tlong=14:30:00（盤後資料更新時間）；若改用 tlong 會誤判成交時間
  - 來源：本地：verify_mis.py 輸出的 quote_time 13:30:00+08:00 與 tlong_tpe 14:30:00+08:00
  - 來源：http://kentyeh.blogspot.com/2015/07/blog-post_16.html：t「揭示時間」、tlong 為系統時間
- **tv（當盤成交量）**：app 未讀取，不影響計算。快照中 tv=560，等於 trade.v=560（收盤集合競價的成交張數）
  - 來源：https://zys-notes.blogspot.com/2020/01/api.html："tv"「當盤成交量」
  - 來源：本地 raw/mis_snapshot.json
- **交易時段邊界 get_market_status**：calendar.py:101-121。以獨立預期值測 12 個時點與週末
  - 來源：本地：08:29:59 closed、08:30 pre_open、09:00 open、13:25 open、13:30:00 open、13:30:00.5 post_close、14:30 post_close、14:30:01 closed、週六 non_trading_day，全部符合
  - 來源：https://support.fugle.tw/trading/trading-dictionary/9648/：「整股盤中開盤 8:30-9:00」「逐筆交易時間：整股盤中時段 9:00-13:25」「整股盤中收盤 13:25-13:30」「整股盤後定價 14:00-14:30」
- **session_progress、SESSION_TOTAL_MINUTES=270 與 vol_ratio_5d 的時間折算**：calendar.py:31、124-161；pipeline.py:1575-1587。app 的算法是 volume×(1/progress)÷前 5 日均量，與量比定義代數等價；另測 09:00=0、11:15=0.5、13:25=0.9815、13:30=1
  - 來源：本地 verify_mis.py 輸出
  - 來源：https://wiki.mbalib.com/zh-tw/%E9%87%8F%E6%AF%94（取自搜尋摘要）：量比＝現成交總手/〔(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〕
- **非即時與官方備援資料不寫成今日K（_records_datable_today）**：quote_service.py:1056-1070、863。輸入 status=official 的資料，回傳 []
  - 來源：本地 verify_mis.py：_records_datable_today(official rows) → []
- **報價快取 TTL 3 秒、輪詢 6 秒**：service.py:44、quote_service.py:244。TTL 比輪詢間隔短，每輪都會真正取價
  - 來源：本地 log：「行情服务已启动, 轮询间隔 6.0s」（2026-09-23 13:27:49）
- **MIS 不提供累計成交金額，amount 設為 None**：mis_provider.py:263。快照欄位中沒有累計金額
  - 來源：本地 raw/mis_snapshot.json 的欄位清單
- **ETF 分鐘K寫入 kline_minute（不寫 kline_etf_minute）**：純台股部署下 repository.py:1344 的 get_etf_symbol_set 是空集合，ETF 全部判定為 stock。寫入與讀取走同一個 resolver，行為一致，沒有資料分裂
  - 來源：本地：kline_etf_minute 沒有任何分區；kline_minute 內含 0050、00878、00885、00893
- **MinuteBarRecorder 的價格過濾、時段窗、累計量重設與收盤 flush**：minute_recorder.py:114-163、95-110。用直接呼叫 _fold 的方式驗證（不寫檔）
  - 來源：本地 verify_mis.py 第 4 段
- **Yahoo 備援的 prev_close（chartPreviousClose）與 volume 單位（股）**：yahoo_provider.py:71-73，對照 Yahoo chart JSON
  - 來源：https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?interval=1d&range=1d：chartPreviousClose 5180.0（等於官方 9/22 收盤）、regularMarketVolume 6425491（單位為股）
- **TaiwanRealtimeService 的官方快照與日K備援**：service.py:155-271。quote_time 設為該日 13:30；盤中一律標記 is_stale；日期缺失時不偽造時間戳
  - 來源：程式碼閱讀：service.py:163-171、221-235

## 無法驗證的項目

- **試撮期間（08:30–09:00）MIS 的 z／trade.z 內容**：沒有盤前快照，而且不得直連 MIS。公開整理只寫到 pz「試算參考成交價」、ps「試算參考成交量」（kentyeh 部落格），沒有說明試撮時 z 與 trade 物件填什麼。app 在盤前把 MIS 標為非即時，不會寫入日K，但自選清單會照單全收 last_price。
- **收盤集合競價（13:25–13:30）期間 z／trade.z 是否填入模擬價**：9/18 的 13:25–13:28 分鐘K價格固定不變，而且不等於收盤價（例如 2454 是 4730，收盤為 4710），比較像最後一筆連續交易價，而非會變動的試算價。但 9/18 跑的是舊版程式碼，沒有原始 MIS 樣本能直接證明。
- **除權息日 MIS 的 y 是參考價還是昨收**：手上沒有除權息日的 MIS 樣本。若 y 是昨收，除息日盤中漲跌幅會把股息算成下跌（例如 00878 8/18 會是 −4.06%，照參考價算是 −1.10%）。官方 STOCK_DAY 在這些日子標為 X（不比價）。
- **MIS 的 ov/oz/oa/ob/fv/ps/ts/ip/it/mt 等欄位語意**：app 沒有使用。唯一的線索是推測 fv＝盤後定價張數：Yahoo 總量 5,909 張減 MIS v 5,902 張正好等於 fv 7 張；除此之外沒有權威定義。
- **9/23 10:10:37–10:12:03 九檔報價同時過期的成因**：log 只記錄筆數（9→8→7→4→0，之後改走官方備援）。無法區分是 MIS 伺服器凍結，還是 t（揭示時間）確實超過 60 秒沒有更新。
- **Yahoo 1200 秒過期門檻是否合理**：Yahoo 沒有公開台股報價的延遲規格；實測 regularMarketTime=13:30:04，看起來接近即時。
- **分鐘K的 OHLC 與分鐘成交量準確度**：證交所沒有公開的逐分鐘官方資料可以比對，第三方分時資料由前端動態產生、不提供歷史；本地分鐘K的覆蓋也不完整（9/23 只有 09:32–11:38 與 13:27–13:28）。
- **第三方的盤中量比與週轉率**：財經網站只提供當下即時值，抓不到 9/23 盤中的歷史值，無法做三方比對。
- **_inject_intraday_signals（分時訊號）**：需要 TickFlow 的分鐘能力與監控規則；使用者的規則數為 0，這條路徑不會執行。
