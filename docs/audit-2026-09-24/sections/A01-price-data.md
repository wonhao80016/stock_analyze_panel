# A01-price-data｜股價與成交資料正確性

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 4 | 6 | 0 | 0 | 0 | 14 | 9 |

## 稽核者摘要

結論：開高低收的解析與落盤全部正確。10 檔上市股票連續 38 個交易日（380 列）、9/23 上市全市場 1,330 檔，以及用 9/23 漲跌欄反推的 9/22 收盤（上市 1,317 檔、上櫃 965 檔），與官方資料都是 0 差異。enriched 的 OHLCV 與 TaiwanDailyStore 一致（6 月以來 184,553 列），raw_close 也等於 close。兩個指數各 34 天完全吻合。ETF（0050、00878、00885、00893）的價格也完全吻合。Yahoo 奇摩的 2454、6196、00878 三方比對同樣一致。

問題出在「資料完整性」與「除權息口徑」：
(1) 9/16 與 9/23 整個上櫃市場（約 1,006 檔）沒有落盤。原因是 TPEx 抓取失敗後，程式仍寫入只有上市的分區，並把該日視為完成，21:40 的補跑也會略過；結果 9/17 上櫃有 973/985 檔的漲跌幅以兩天前的收盤計算（P1）。
(2) 台股完全沒有除權息調整：除權息日的 change_pct 以除權息前收盤計算（956 個事件中 350 個方向相反，6669 除權當日顯示 -66.5%），跨除權息缺口的指標與訊號也跟著失真，這違反 CONTRIBUTING 3.2 的前復權契約（P1）。
(3) 除權息日的漲跌停價用除權息前收盤、而不是除權息參考價計算（P2）。
(4) 16:30 當日抓取的上市成交量／金額少了約 17:00 前才併入的量，之後永遠不會重抓（P2）。
(5) data_integrity 只檢查台股部署不寫入的 A 股表：一方面看不到上述缺漏；另一方面在 9/24～9/29 會對 kline_daily 誤報「缺失」，而台股能力集含 KLINE_DAILY_BATCH，會觸發一個空轉的修復任務，並讓開啟即時行情時回 409（P2）。
(6) 另有 5 項 P3。

使用者的 9 檔自選全為上市：沒有直接受 (1) 影響，但受 (2)（2379 9/8、00878 8/18 等）與 (4)（2454 連 5 天偏低、6196 9/17 少 17%）影響。所有腳本都在 scratchpad\audit\work\A01\c1～c10，repo 工作樹未變動。

## 問題清單

### [P1 → P1] F1 上櫃抓取失敗時仍寫入只有上市的分區，並視為當日完成；9/16、9/23 整個上櫃市場缺漏且不會自動補回（確認成立）

- **主張**：OfficialDailySnapshotAdapter.fetch_date 分別吞掉上市與上櫃的例外，只回傳成功的那一半。refresh_dates 把這一半寫進分區，記為 fetched；之後只要該日有分區就跳過，daily_update 仍回報 daily=success。結果 2026-09-16 與 09-23 的 store 與 enriched 都沒有任何 .TPEX 列。21:40 那班的註解寫明要給 16:30 失敗「第二次機會」，但會把已落盤的日期略過，所以這個機會實際不存在
- **正確的定義或數值**：任一交易所抓取失敗時，不應把該日視為完成：應該不寫入，或逐交易所記錄完整度並在下一班重抓；daily 狀態應為 partial／failed
- **app 實際行為**：上櫃約 1,006 檔在兩個交易日完全沒有日K。下一個交易日（9/17；9/24 也會發生）的 prev_close、change_pct、均線與 MACD 等以缺一天的序列計算；選股的 read_latest_per_symbol 會把上櫃的 9/22 列與上市的 9/23 列混在同一個 daily_as_of=9/23 下
- **對使用者的影響**：自選 9 檔全為上市，沒有直接受影響。選股、市場概覽、異常診斷等全市場功能，在 9/23 起缺少整個上櫃，而且 9/24 起所有上櫃的漲跌幅會跨兩天計算。任何從 Screener 點開的上櫃股票 K 線都缺兩根，錯誤會一直存在，直到有人手動用 --force 重抓
- **修法建議**：fetch_date 在任一交易所失敗時拋出例外或回報缺漏的交易所；refresh_dates 依交易所檢查完整度（例如 TPEx 列數低於前一日的 50% 視為不完整），不完整就記入 failed_dates 而不是跳過；21:40 那班強制重抓最新一個交易日。另外，share_capital_store 在 9/23 16:30 也有同型問題：TPEx 失敗仍以 1,095 檔覆寫股本，見 backend.log:37262-37263，建議一併處理
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:103-124（try/except 各自 logger.warning，回傳部分 frame）
  - backend/app/taiwan/daily_refresh.py:150,167-179（日期在 available 就跳過；部分資料也寫入並記為 dates_fetched）
  - backend/app/taiwan/daily_update.py:282,302-311（只從 d_max 往後補；沒有 failed_dates 就判 success）
  - backend/app/jobs/daily_pipeline.py:192-194（註解：21:40 要讓 16:30 失敗的那班有第二次機會，但已落盤的日期會被 refresh 略過）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - c2_coverage.py：661 個分區的 tpex_min 為 0，只有 2026-09-16（TWSE 1330／TPEx 0，mtime 16:30:22）與 2026-09-23（1330／0，mtime 16:30:05）；其他日期 TPEx 約 1,003～1,009 列
  - data\backend.log:37258「TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body」；37265「daily=success」；45477（21:40 那班）「is_fully_current=True」
  - c3：enriched date=2026-09-16 有 1,319 列、09-23 有 1,323 列，tpex 皆 0
  - c6_tpex_gap.py：enriched 9/17 的 985 列上櫃中，973 列 prev_close 等於 9/15 收盤。6488：app change_pct -0.22%，官方 (912/937-1) = -2.67%；8069：app -0.34%，官方 -1.69%
  - c1：tradingStock 的 6488／8069 在 9/16、9/23 有資料，store 缺列
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：OfficialDailySnapshotAdapter.fetch_date（snapshot_provider.py:103-124）分別吞掉上市與上櫃的例外，只回傳成功的那一半。refresh_dates（daily_refresh.py:167-177）把這一半寫進分區並計為 dates_fetched；run_update 之後只從 d_max 往後補（daily_update.py:282），因此該日不會再被重抓，daily 狀態仍回報 success。結果 2026-09-16 與 09-23 的 store 與 enriched 都沒有任何 .TPEX 列（上市 1330 列、上櫃 0 列）。9/17 上櫃有 973／985 列的 prev_close 取自 9/15，例如 6488 的 app 漲跌幅是 -0.22%，官方是 -2.67%。Screener 會把上櫃 9/22 的列放在 daily_as_of=9/23 之下。daily_pipeline.py:194-195 說的 21:40「第二次機會」，只在兩個交易所都失敗、沒有寫出分區時才存在；部分失敗時不存在（9/23 21:40 那班未重抓，卻回報 is_fully_current=True）。另外，兩個交易所都失敗時，該日會被算成 dates_skipped，daily 同樣顯示 success。手動修復必須對每個日期執行 --target-date YYYY-MM-DD --force；只加 --force 只會重抓最新的 target 日。9/16 的 log 已不存在，失敗原因只能從分區內容推斷。附帶的 share_capital 部分覆寫在 21:40 已自行恢復（1988 檔），屬暫時性的次要問題。
    - 理由：我逐一讀過引用的程式碼、實際讀了 parquet 和 log，也對照了官方參考資料與線上來源。finding 的核心主張全部成立。

一、程式碼路徑：
- snapshot_provider.py:103-124 對上市、上櫃各自 try/except，只記 warning，然後回傳成功的那一半。
- daily_refresh.py:150、167-170 只要分區存在就略過；172-177 遇到非空的一半資料也會寫入，並計入 dates_fetched。
- daily_update.py:282 只從 d_max 往後補；308-311 沒有 failed_dates 就判 success。
- daily_pipeline.py:194-195 的註解說 21:40 那班是「第二次機會」。但 9/23 21:40 那班的 log 裡完全沒有 MI_INDEX 或 dailyQuotes 的請求（d_max=9/23，直接走 'Daily store already current'），卻回報 is_fully_current=True。
- daily_store.read_latest_per_symbol（daily_store.py:177-196）和 screener.py:207、215 會把上櫃 9/22 的列放在 daily_as_of=9/23 之下。
- 系統沒有自癒路徑：data_integrity 只掃 A 股 kline_daily 表；bootstrap.update_to_latest 同樣只往後補；K 線 API 也不回補中間的缺口。

二、資料：
- 661 個分區中，只有 2026-09-16 與 09-23 的 TPEX=0（上市都是 1330 列），mtime 分別是 16:30:22 與 16:30:05。
- enriched 9/16 共 1319 列、9/23 共 1323 列，上櫃都是 0。
- enriched 9/17 的 985 列上櫃中，973 列的 prev_close 等於 9/15 收盤。原因是 pipeline.py:388 用 prev_close = close.shift(1).over('symbol')。
- 官方 tradingStock 資料：6488 在 9/16 收 937、9/17 收 912（漲跌 -25），8069 在 9/16 收 148、9/17 收 145.5。app 計算的 change_pct 是 -0.22% 與 -0.34%，正確值應為 -2.67% 與 -1.69%。
- 官方 9/23 dailyQuotes 確實有資料（11,696 列），HiStock 也顯示 6488 收 950、▼21。可見缺漏來自抓取失敗，不是休市。

三、定義：漲跌幅＝(收盤－前一交易日收盤)／前一交易日收盤。缺一天就等於跨兩天計算，屬於錯誤的金融口徑。

四、嚴重度：P1 恰當。約 1,000 檔上櫃股在兩個交易日完全缺漏，下一個交易日的漲跌幅口徑錯誤，且會永久留存、狀態卻回報 success。這違反 CONTRIBUTING 第 11 節第 8 點「部分寫入時不得產生錯誤狀態」。沒有到 P0：自選 9 檔都是上市，也沒有監控規則，不涉及交易下單。

需要修正的細節：
1. daily_pipeline 的行號是 194-195，不是 192-194。
2. 「第二次機會不存在」只對部分失敗成立。兩個交易所都失敗時 df 是空的，不會寫分區，21:40 仍會重抓。不過這種情況 refresh_dates 會把該日算成 dates_skipped，daily 狀態同樣顯示 success，是另一個次要面向。
3. 9/16 當天的 log 已經不在（backend.log.1／.3 從 9/17 開始，沒有 .2）。9/16 上櫃失敗的具體原因（例外或空表）無法查證，只能從分區內容與 mtime 推斷走的是同一條路徑。
4. 手動修復只加 --force 不夠。沒有 --target-date 時只會重抓 target 當天（9/23）；9/16 必須用 --target-date 2026-09-16 --force。
5. share_capital 的附帶問題確實存在：16:30 那班以 1095 檔覆寫，把上櫃舊值丟掉。但 21:40 那班已恢復為 1988 檔（log 顯示 21:40:02），屬於數小時內自癒的暫時性問題，程度低於主問題。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:103-124：兩段 try/except 各自 logger.warning，只要有一邊 frames 非空就回傳部分資料
      - backend/app/taiwan/daily_refresh.py:150,167-170：分區已存在就略過（dates_skipped）；172-179：df 非空就 write_batch 並把 dates_fetched 加 1，df 為空則記為 dates_skipped，不是 failed
      - backend/app/taiwan/daily_update.py:282-286：d_range=resolve_missing_date_range(d_max,...)，d_max>=target 時 note='Daily store already current'；308-311：沒有 failed_dates 就判 success
      - backend/app/jobs/daily_pipeline.py:194-195 的註解『讓 16:30 那班若失敗…仍有第二次機會; 已落盤的日期會被 refresh 略過』
      - backend/app/indicators/pipeline.py:388：prev_close = pl.col('close').shift(1).over('symbol')，缺一天就跨兩天計算
      - backend/app/taiwan/daily_store.py:177-196 與 app\taiwan\screener.py:207,215：各 symbol 取最新列，daily_as_of 取全體的 max
      - 本地指令 cov.py（uv run python，讀 data\taiwan\daily）：partitions 661；tpex==0 只有 ('2026-09-16',1330,0) 與 ('2026-09-23',1330,0)；前後日期的 TPEx 約 1005～1009 列；mtime 分別為 2026-09-16 16:30:22 與 2026-09-23 16:30:05
      - 本地指令 enr.py：enriched 2026-09-16 共 1319 列、tpex 0；2026-09-23 共 1323 列、tpex 0；9/17 上櫃 985 列中 prev_close==9/15 收盤的有 973 列；6488.TPEX 9/17 close 912、prev_close 914、change_pct -0.002188；8069.TPEX 145.5／146.0／-0.003425；store 9/16、9/23 的 6488／8069 皆 0 列；各 symbol 最新日期為 TPEX 9/22（1009 檔）、TWSE 9/23（1330 檔）
      - 官方參考資料 reference\csv\tpex_trading_stock_202608_202609.csv：6488 在 2026-09-16 close 937、09-17 close 912（漲跌 -25.0）；8069 在 09-16 close 148.0、09-17 close 145.5（漲跌 -2.5），官方漲跌幅 -2.67%／-1.69%
      - 官方參考資料 reference\csv\tpex_daily_quotes_20260923.csv 共 11,696 列，證實 9/23 上櫃有交易且已公布
      - data\backend.log:37258『TPEx snapshot fetch failed for 2026-09-23: peer closed connection…』；37265『daily=success』；37262-37263 share capital 的 TPEX 失敗後仍以 1095 檔覆寫；21:40 那班沒有 MI_INDEX／dailyQuotes 請求，21:40:02 share capital 恢復為 1988 檔；45477『is_fully_current=True』
      - data 目錄下 backend.log.1／.3 分別從 2026-09-17 16:47、09-17 11:14 開始，沒有 9/16 的 log，9/16 上櫃失敗的原因無法直接查證
      - backend/app/services/data_integrity.py:46 _DAILY_TABLES 只含 A 股 kline_daily 系列；bootstrap.py:429-449 update_to_latest 只從 latest+1 往後補，兩者都不會修補中間缺口
      - 線上定義 https://www.jendow.com.tw/wiki/%E6%BC%B2%E8%B7%8C%E5%B9%85 ：『漲跌幅=漲跌值/昨收盤*100%』、『一般情況：開盤參考價=前一交易日收盤價』
      - 線上佐證 https://histock.tw/stock/6488 ：當日股價 950 元，漲跌 ▼21（-2.16%），與官方 9/23 收盤一致，證實 9/23 上櫃有行情
      - CONTRIBUTING.md 第 11 節：P1＝核心流程不可用、錯誤金融口徑；複審第 8 點『部分寫入時不得產生錯誤狀態』
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：核心主張成立，數值全部重現。OfficialDailySnapshotAdapter.fetch_date 分別吞掉上市與上櫃的失敗，refresh_dates 把只有上市的 frame 寫入分區並記為 fetched（failed_dates 為空），daily_update 因而判為 success。之後 d_max 等於 target，resolve_missing_date_range 回傳 None，21:40 那班完全沒有重抓日K。結果 store 的 2026-09-16 與 09-23 分區都是 TWSE 1330 列、TPEx 0 列（依官方 9/23 dailyQuotes，應有 1007 檔上櫃），enriched 兩日的 TPEx 也都是 0。9/17 的上櫃有 973/985 列以 9/15 收盤作為 prev_close，例如 6488 的 app 漲跌幅為 -0.22%，官方為 -2.67%；8069 為 -0.34%，官方 -1.69%；6488 的 MA5 為 909，官方序列為 913。
需要修正的細節有五點：
(a) 管線註解在 daily_pipeline.py:194-195。
(b) 選股並沒有少掉上櫃。它仍列出 1009 檔上櫃，但用的是 9/22 的列，掛在 daily_as_of=9/23 下，而且訊號欄全是 null（enriched 9/23 沒有 TPEx）。整個缺上櫃的是市場概覽，因為它只讀當日分區。
(c) 股本問題只在 9/23 16:30～21:40 之間存在，21:40 已恢復為 1988 檔，屬暫時性問題，與日K缺口不會補回的性質不同。
(d) 9/16 當天沒有 log，TPEx 是例外還是空表無法確認，但兩者造成的結果相同。
(e) 「9/24 也會發生」是依機制推論，尚未有資料可以驗證。
    - 理由：finding 的核心主張，以及它列出的每一個數值，我都用自己的腳本逐一重現，數字完全一致：
(1) store 共 661 個分區，TPEx 為 0 的只有 9/16（TWSE 1330／TPEx 0，mtime 16:30:22）和 9/23（1330／0，mtime 16:30:05）。其他日期自 2026-08 起 TPEx 為 1003～1009 列，前後的 9/15、9/17 都是 1006。
(2) enriched 9/16 共 1319 列、9/23 共 1323 列，TPEx 都是 0。
(3) enriched 9/17 有 985 列 TPEx，其中 973 列的 prev_close 等於 store 9/15 收盤；剩下 12 列是 9/15 本身沒有資料的標的。對照組：同一分區的 TWSE 有 1313/1323 列等於 9/16 收盤。
(4) 6488 的 app change_pct 為 -0.2188%（912/914-1），依官方 tradingStock 應為 912/937-1 = -2.6681%。8069 的 app 值為 -0.3425%，官方為 145.5/148-1 = -1.6892%。另外我補算了 6488 在 9/17 的 MA5：app 為 909.0，官方序列為 913.0。
(5) log 行號 37258、37262、37263、37265、45477 的內容都與 finding 引述相符。21:40 那班（45470-45477）完全沒有發出 MI_INDEX 或 dailyQuotes 請求，因為 resolve_missing_date_range(9/23, 9/23) 回傳 None，程式走 daily_update.py:283-286 的「already current」分支。
機制部分，我拿真實程式 OfficialDailySnapshotAdapter + TaiwanDailyRefreshService 搭配記憶體內的假 store、假 client，把官方 9/23 MI_INDEX 原始 JSON 餵進去，TPEx 則讓它丟出 RemoteProtocolError。結果寫入 1330 列、TPEx 0 列，dates_fetched=1、failed_dates=[]，依 daily_update.py:308-311 判為 success。第二次呼叫 refresh_dates 沒有發出任何 HTTP 請求，記為 skipped。對照組讓 TPEx 正常回應，會寫入 2337 列，其中 TPEx 1007 列。因此 9/23 應有約 1007 檔上櫃資料，實際為 0。
不會自動補回這點也成立：bootstrap.update_to_latest 與 post-bootstrap sync 都只從 latest+1 往後補；data_integrity 只掃 kline_daily 系列表，不看 taiwan/daily，而且只報尾部缺口；K 線 API（kline.py 的 _read_taiwan_local_daily）直接讀 store，中間缺日不會觸發即時抓取。
需要修正的只有次要細節：
a) daily_pipeline 的註解位於 194-195 行，不是 192-194。
b) user_impact 把「選股」歸入「缺少整個上櫃」，這不準確。依 read_latest_per_symbol 的邏輯重算：選股仍列出 1009 檔上櫃，但用的是 9/22 的列，並掛在 daily_as_of=9/23 下；enriched 9/23 沒有 TPEx 列，所以這些上櫃股的訊號欄全是 null。這和 finding 自己在 actual 欄的描述一致，user_impact 與 actual 互相矛盾。真正整個缺上櫃的是市場概覽（market_intelligence.get_snapshot 只讀 target 當日）。
c) share_capital 的附註只對一半：9/23 16:30 確實只寫入 1095 檔。但股本每一班都會重抓，21:40 已恢復為 1988 檔（log 45474-45475）。目前檔案內 TWSE 1095、TPEX 893，屬於暫時性問題，與日K缺口不會補回的性質不同。
d) 9/16 當天沒有 log 可查（現存 log 最早從 9/17 11:14 開始），無法判定當時 TPEx 是丟出例外還是回傳空表。不過兩條路徑在 fetch_date 裡的結果相同，都是只寫入上市。
e) 「9/24 也會發生」是依機制推論（推測），目前 9/24 還沒有資料可以驗證。
嚴重度維持 P1：整個上櫃市場有兩個交易日的日K靜默缺失，約 1000 檔的漲跌幅、均線、MACD 口徑錯誤，而且回報為 success、不會自動修復。使用者自選的 9 檔都是上市股，沒有直接受影響。
    - 證據：
      - 覆蓋率腳本 AUDIT_SCRATCH/work/verify-f1tpex/v1_coverage.py（輸出 v1_out.txt）：partition count 661；tpex==0 只有 2026-09-16（1330/1330/0，mtime 16:30:22）與 2026-09-23（1330/1330/0，mtime 16:30:05）；2026-08 起非零的 TPEx 為 1003～1009 列；enriched 9/16 total 1319、tpex 0，9/23 total 1323、tpex 0，9/17 tpex 985
      - v2_gap.py（輸出 v2_out.txt）：enriched 9/17 TPEx 985 列，prev_close 等於 store 9/15 收盤的有 973 列，9/15 無資料的 12 列；對照組 TWSE 1313/1323 列的 prev_close 等於 9/16 收盤
      - v2_gap.py：6488 官方 tradingStock 收盤為 9/15 914、9/16 937、9/17 912、9/23 950；store 9/16、9/23 各 0 列；enriched 9/17 的 prev_close=914.0、change_pct=-0.002188，官方應為 912/937-1=-2.6681%。8069 官方收盤為 9/15 146、9/16 148、9/17 145.5；app change_pct -0.3425%，官方 -1.6892%
      - 6488 MA5（9/17）：app enriched ma5=909.0（序列缺 9/16）；依官方 tradingStock 收盤 902/900/914/937/912 算出 913.0
      - 官方 9/23 dailyQuotes（reference/csv/tpex_daily_quotes_20260923.csv，11696 列）與 security master 支援的 TPEx 1012 檔交集為 1007 檔；store 9/22 的 1009 檔中有 1007 檔出現在官方 9/23 資料
      - v3_mechanism.py（輸出 v3_out.txt，真實 adapter 與 refresh_dates 搭配假 store／client）：TPEx 丟 RemoteProtocolError 時 stats={'dates_fetched':1,'total_rows_written':1330,'failed_dates':[]}，寫入 twse 1330、tpex 0，依 daily_update.py:308-311 判為 success；第二次呼叫 http calls=[]、dates_skipped=1；對照組 TPEx 正常時寫入 2337 列（tpex 1007）
      - backend/app/taiwan/providers/snapshot_provider.py:105-118 各自 try/except 只記 warning，120-124 只要有一邊有資料就回傳
      - backend/app/taiwan/daily_refresh.py:150,167-170 日期已存在即 skip；173-177 非空即 write_batch 並累加 dates_fetched
      - backend/app/taiwan/daily_update.py:136-137 在 d_max>=target 時回傳 None；282-286 判為 'Daily store already current' success；308-311 沒有 failed_dates 就判 success
      - backend/app/jobs/daily_pipeline.py:194-195 註解「讓 16:30 那班若失敗…仍有第二次機會; 已落盤的日期會被 refresh 略過」（finding 引用的 192-194 差了 1 行）
      - data/backend.log:37258 TPEx snapshot fetch failed for 2026-09-23；37262-37263 股本 TPEX 失敗，refreshed 1095 symbols；37265 daily=success；45477 21:40 is_fully_current=True；21:40 那班（grep '^2026-09-23 21:4'）的 MI_INDEX 與 dailyQuotes 請求數為 0
      - backend.log:45474-45475：21:40 股本 TPEX returned 893 rows，refreshed 1988 symbols；目前 share_capital.parquet 為 TWSE 1095、TPEX 893（mtime 2026-09-23 22:07），16:30 的股本缺漏已自癒
      - v4_latest.py（輸出 v4_out.txt）依 daily_store.py:177-196 的邏輯重算：daily_as_of=2026-09-23；TPEX 1009 檔最新日為 9/22、1 檔為 9/17；TWSE 1330 檔為 9/23；enriched 9/23 可供 _join_signals（screener.py:587-605）使用的 TPEX 列為 0
      - backend/app/taiwan/market_intelligence.py:383 只讀 read_range(target,target)，因此市場概覽在 9/23 只有上市
      - 不會自動補回：bootstrap.py 的 update_to_latest 從 latest+1 開始；data_integrity.py 的 scan_recent_integrity 只掃 kline_daily、kline_etf_daily、kline_index_daily，且只報 day>latest 的尾部缺口；kline.py 的 _read_taiwan_local_daily 直接讀 store
      - 線上交叉核對：https://histock.tw/stock/6488 顯示 2026/09/23 價格 950，▼21（-2.16%）；https://tw.stock.yahoo.com/quote/6488.TWO/technical-analysis 顯示「95021.00(2.16%)」「收盤 | 2026/09/23 14:30 更新」，都與官方 tradingStock 9/23 收盤 950、漲跌 -21 相符。9/16 的歷史列在這兩站都抓不到（前端動態產生；/history 回 404）
      - 9/16 無 log：現存 backend.log.3、.1 與 backend.log 最早從 2026-09-17 11:14 開始，無法判定 9/16 的 TPEx 是例外還是空表

### [P1 → P1] F2 台股完全沒有除權息調整：除權息日的 change_pct 以除權息前收盤計算，指標與訊號跨除權息缺口失真（確認成立）

- **主張**：TaiwanEnrichedRefreshService 呼叫 compute_enriched 時傳入 factors=pl.DataFrame()，hybrid_provider.get_adj_factors 也固定回傳空表，所以 enriched 的 open/high/low/close 就是原始價，close 等於 raw_close。change_pct = close/close.shift(1) - 1，除權息日的分母是除權息前收盤，而不是官方的除權息參考價。這違反 CONTRIBUTING.md 3.2「enriched 的 open/high/low/close 為前復權價格」
- **正確的定義或數值**：依 CONTRIBUTING 3.2，應以前復權價計算指標，並保留 raw_*；至少除權息日的 change_pct 應以除權息參考價為分母，與交易所及券商的顯示一致
- **app 實際行為**：close 等於 raw_close；除權息日的漲跌幅把股利也算成下跌；MA、MACD、KD、RSI、動能、布林與均線突破／跌破訊號，都在原始價的除權息缺口上計算
- **對使用者的影響**：K 線卡片、選股與排行的漲跌幅在除權息日顯示錯誤的數字（6669 顯示 -66.5%）；均線跌破、N 日新低等訊號在除權息日大量誤觸。自選中的 2379、00878、3545、6196、2454、0050 在最近幾個月都有除權息日受影響；6669 這類配股或分割，會讓 60 日內的指標全部失真
- **修法建議**：以 TWT49U 與 TPEx 除權息表建立台股的 adj_factor（factor = 參考價／前收），傳入 compute_enriched(factors=...)，讓 OHLC 前復權、raw_* 保留原始價；若短期不做復權，至少讓除權息日的 prev_close／change_pct 改用官方參考價
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95（factors=pl.DataFrame()）
  - backend/app/taiwan/providers/hybrid_provider.py:144-152（get_adj_factors 回傳空表）
  - backend/app/indicators/pipeline.py:1022-1024（只有 factors 非空才前復權）
  - backend/app/indicators/pipeline.py:503-504（change_pct = close/close.shift(1)-1）
  - CONTRIBUTING.md:100-103
- **線上證據**：
  - 官方 TWT49U（manifest 下載）的公式：「除權息參考價 = (除權息前收盤價 − 息值 + 現增認購價×現增配股率)/(1+無償配股率+現增配股率)」；STOCK_DAY 在除權息日的漲跌欄是「X0.00」（不比價），例如 2379 2026-09-08、00878 2026-08-18、2330 2026-09-16
  - https://tw.stock.yahoo.com/news/除權息-參考價-除權-除息-除息試算-080038623.html（WebFetch 摘要）：「除息前股價 – 現金股利」「(除權息前一日收盤價 – 現金股利) ÷ (1 + 股票股利 ÷ 10)」
  - WebSearch「除息日 漲跌 是以 參考價 計算」結果摘要：「除權息交易日當天…這個扣掉股利之後的平盤價，就是除權息參考價」「出現向下的跳空缺口」（來源為搜尋摘要，未逐字核對原頁）
- **本地計算證據**：
  - c7_exdiv.py（956 個上市除權息事件，2026-06～09）：app change_pct 減掉以參考價計算的漲跌，差距最小 -66.348 個百分點、中位數 -2.824、最大 +1.453；350 個事件的 app 為負值，而以參考價計算為上漲
  - 6669 緯穎 9/2 除權（前收 7,800，參考價 2,614.99）：app change_pct -0.665385；momentum_20d 從 +0.257 變成 -0.577；rsi_14 從 77.4 變成約 25，之後連 3 週不動；9/23 的 ma20 為 3,288.25，股價 2,105；signal_n_day_low 在 9/2 之後重複觸發
  - 自選股：2379 9/8 app -5.08%（參考價基準 -1.70%），且觸發 signal_ma5_breakdown；00878 8/18 app -4.06%（-1.10%），觸發 ma5 與 ma20 跌破；3545 8/4 app -0.40%（+1.26%）；6196 6/24 app -0.18%（+1.02%）；2330 9/16 app -0.21%（+0.08%）
  - 956 個除權息日中有 296 個觸發 signal_ma20_breakdown，其中 131 個以參考價計算其實是上漲日
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：成立。台股 enriched 完全沒有除權息調整：enriched_refresh.py:95 與 api/kline.py:435 都傳入空的 factors，所以 close 等於 raw_close，prev_close 就是除權息前的原始收盤。這違反 CONTRIBUTING.md:100 的前復權契約。除權息日的 change_pct 以前收為分母，而交易所當天以參考價為比價基準（STOCK_DAY 為 X0.00 不比價；TWT49U 的漲跌停價以參考價計算）。956 個上市事件中，差距中位數 −2.82 個百分點，350 筆的正負號相反；MA、RSI、動能與均線跌破、N 日新低等訊號也跨越除權息缺口計算，例如 6669 在 9/2 為 −66.5%，rsi_14 從 77 掉到 26 後停滯。補充一個原 finding 未提的面向：_add_limit_signals（enriched_refresh.py:169）也以前收計算漲跌停，已發布表中有 4 個除權息日誤判為跌停（5906、6924、1442、2542）。影響範圍的修正：監控規則為 0 條，不會產生告警，影響集中在選股、策略、排行、歷史 K 棒與指標。K 線卡片在除權息當天是否顯示錯誤的漲跌幅，取決於 MIS 的 y 欄是否已是參考價（推測當天可能正確）。原始價 K 線本身在台灣是常見的顯示慣例，錯誤在於違反 repo 自己的前復權契約，以及漲跌幅與指標的口徑偏離官方參考價基準。嚴重度維持 P1。
    - 理由：【程式碼】親自讀過程式碼，行號都沒有偏移。enriched_refresh.py:95 以 compute_enriched(raw, factors=pl.DataFrame(), ...) 呼叫；pipeline.py:1023-1024 只有 factors 非空才前復權；pipeline.py:504 的 change_pct = close/close.shift(1)-1；pipeline.py:388 的 prev_close = close.shift(1)。K 線 API 的本地路徑（api/kline.py:435）同樣傳空 factors。hybrid_provider.py:144-152 回傳空表，docstring 寫「Phase 2 records raw price semantics」，但 docs/ 裡找不到任何「台股刻意採原始價、偏離 CONTRIBUTING 3.2」的決策記錄。dividend_events.py 只被 fundamentals.py:304 使用，沒有用在價格調整。CONTRIBUTING.md:100 明文要求 enriched 的 OHLC 為前復權，所以違反契約這點成立。

【正確定義】官方 TWT49U（manifest 參考資料）的參考價公式，以及它依參考價算出的當日漲跌停價：2379 參考價 704 對應漲停 774／跌停 634，而不是以前收 729 計算。STOCK_DAY 在除權息日的漲跌欄是「X0.00」（不比價），可見交易所當日的比價基準是參考價，不是前收。另外找到兩個與 finding 不同的來源。證基會（fund.gov.tw）定義參考價：「將每股股價按照發放現金的多寡，予以同等幅度的降低」「除息參考價格＝前一交易日收盤價－現金股利」。玩股網說明除權息對指標的影響：「除權息除了均線有差異外，其他技術指標(KD、RSI及MACD等)也會受影響」。

【本地獨立重算】兩條路徑都重算過：用 compute_enriched 純計算，也直接讀已發布的 enriched parquet。finding 引用的數字全部一致：956 個上市事件（TWT49U 共 994 筆，其中 10 筆沒有分區、28 筆沒有該列）；956 筆的 prev_close 全部等於官方的除權息前收盤，close 全部等於 raw_close。app 與參考價基準的差距，最小 −66.348、中位數 −2.824、最大 +1.453 個百分點。350 筆 app 為負而以參考價計算為上漲；296 筆觸發 ma20 跌破，其中 131 筆以參考價計算其實上漲。6669 在 9/2 的 change_pct 為 −0.665385，momentum_20d 從 0.257 變成 −0.577，rsi_14 從 77.44 變成 26.41，之後一直停在約 25～26；9/23 的 ma20 為 3288.25，n_day_low 重複觸發。自選股的部分：2379 在 9/8 為 −5.075%（參考價基準 −1.705%），觸發 ma5 與 ma10 跌破；00878 在 8/18 為 −4.059%（−1.100%），觸發 ma5、ma10、ma20 跌破；3545 為 −0.399%（+1.258%）；6196 為 −0.185%（+1.020%），並觸發 ma10 跌破。

【新面向，原 finding 未提】enriched_refresh.py:169 的 _add_limit_signals 以 prev_close（除權息前的原始收盤）當漲跌停基準。已發布表中有 4 個除權息日 signal_limit_down=True：5906（9/10）、6924（9/15）、1442（9/23）、2542（9/23）。但以官方 TWT49U 的跌停價計算，4 筆都沒有觸及（0/4），屬於假跌停訊號。N5 只記錄盤中漲跌停訊號沒有計算，沒有涵蓋這一點。

【影響範圍修正】使用者情境是監控規則 0 條，所以誤觸的訊號不會產生告警，影響落在選股、策略、排行、歷史 K 棒的 change_pct 與指標。finding 說 K 線卡片在除權息日顯示 −66.5%，這點可能誇大（推測）：即時行情開啟時，_maybe_inject_live_candle（api/kline.py:564-573）會用報價的 change_pct 覆蓋今日蠟燭，而 MIS 報價的分母是 y 欄（mis_provider.py:160-174）。若 y 在除權息日就是參考價，卡片當天顯示的其實是正確值。參考快照裡沒有同日除權息的標的，無法證實 y 是否等於參考價。另外，台灣券商與財經網站的 K 線普遍預設顯示未還原價，還原權息是另一個選項（玩股網有按鈕；HiStock 另有還原權息頁），所以「顯示原始價」本身不算錯。真正的錯誤在兩處：違反 repo 自己的前復權契約；除權息日漲跌幅與指標的口徑，偏離交易所以參考價為基準的規則。這屬於 CONTRIBUTING 第 11 節定義的 P1（錯誤的金融口徑），維持 P1。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95：`enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`
      - backend/app/indicators/pipeline.py:1023-1024：只有 factors 非空才呼叫 _apply_adj_factor；pipeline.py:504：change_pct = close/close.shift(1)-1；pipeline.py:388：prev_close = close.shift(1)
      - backend/app/taiwan/providers/hybrid_provider.py:144-152：get_adj_factors 回傳 pl.DataFrame()，docstring 為「Phase 2 records raw price semantics」；docs/ 下沒有對應的決策記錄
      - backend/app/api/kline.py:435：本地路徑也是 compute_enriched(local, factors=pl.DataFrame())
      - CONTRIBUTING.md:100：「enriched 的 `open/high/low/close` 為前復權價格。」
      - 官方 TWT49U（reference/csv/twse_twt49u_20260601_20260923.csv）：2379 在 2026-09-08 前收 729、參考價 704、漲停 774、跌停 634；00878 在 2026-08-18 前收 33.75、參考價 32.74；6669 在 2026-09-02（權）前收 7800、參考價 2614.99。STOCK_DAY 在除權息日的 change_raw 為「X0.00」
      - https://www.fund.gov.tw/News_Content.aspx?n=708&s=16888（證基會）：「係指公司在發放現金股利時，將每股股價按照發放現金的多寡，予以同等幅度的降低」「除息參考價格＝前一交易日收盤價－現金股利」
      - https://www.wantgoo.com/blog/98845/post/76（玩股網）：「除權息除了均線有差異外，其他技術指標(KD、RSI及MACD等)也會受影響」「還原權息這功能，能讓股價不易出現失真的情況」
      - 本地重算 stats.py（讀已發布的 kline_daily_enriched 與 TWT49U）：matched 956；prev_close==官方前收 956/956；close==raw_close 956/956；差距 −66.348／−2.824／+1.453 個百分點；app 負值但以參考價計算為上漲 350；ma20 跌破 296，其中以參考價計算上漲 131；ma5 跌破 335；n_day_low 168
      - 本地重算 check.py（compute_enriched 純計算）：6669 在 9/2 change_pct −0.665385、momentum_20d 0.257051→−0.577328、rsi_14 77.44→26.41、9/23 ma20 3288.25、close 2105；2379 在 9/8 為 −5.075%（參考價基準 −1.705%），fired=[ma5_breakdown, ma10_breakdown]；00878 在 8/18 為 −4.059%（−1.100%），fired=[ma20, ma5, ma10 breakdown]
      - 新面向：backend/app/taiwan/enriched_refresh.py:169 以 `ref = pl.col("prev_close")` 計算漲跌停。已發布表中 5906（9/10）、6924（9/15）、1442（9/23）、2542（9/23）的 signal_limit_down=True，但收盤價都高於官方跌停價（例如 1442 收 27.8，官方跌停 25.9），0/4 真正跌停
      - 影響範圍的限制：api/kline.py:564-573 的即時蠟燭 change_pct 取自報價；mis_provider.py:160-174 的分母為 MIS 的 y。除權息當天卡片是否正確取決於 y 是否等於參考價，參考快照中沒有同日除權息的標的，無法證實（推測）
      - 腳本與輸出：AUDIT_SCRATCH/work/verify_f2_defcode/（check.py、out.txt、stats.py、stats_out.txt）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 完全沒有做除權息調整。TaiwanEnrichedRefreshService 呼叫 compute_enriched 時傳入空的 factors（enriched_refresh.py:95），hybrid_provider.get_adj_factors 也固定回傳空表，因此已存檔的 enriched 自 2026-04-01 起 close 全部等於 raw_close，違反 CONTRIBUTING.md:100。除權息日的 change_pct 分母是官方的除權息前收盤價（956/956 相符），不是除權息參考價。

漲跌幅差距：在 956 個可比對的上市除權息事件（2026-06～09）中，app 與以參考價計算的漲跌差距最小 -66.35、中位數 -2.82、最大 +1.45 個百分點；有 350 個事件 app 顯示下跌，以參考價計算其實是上漲。

指標與訊號：均線、RSI、動能、布林、N 日高低點與交叉訊號都在原始價缺口上計算。以前復權價反事實重算，除權息日的 296 個 ma20_breakdown 中有 222 個是誤觸（ma5_breakdown 誤觸 206/335、n_day_low 誤觸 132/168、boll_breakdown_lower 誤觸 219/257），同時漏報 ma5_breakout 125 次、n_day_high 65 次。2026-06-01 以後全期間，只有原始價才觸發的 n_day_low 有 1,389 列，只有前復權才觸發的 n_day_high 有 1,011 列。

6669 在 9/2 除權（前收 7,800，參考價 2,614.99）：app 顯示 -66.54%，以參考價計算應為 -0.19%。momentum_20d 從 +0.257 變成 -0.577，前復權應為 +0.261。rsi_14 從 77.4 掉到 26.4，並停在 25～27 三週，前復權應為 77.1 到 42.2。9/23 的 ma20 為 3,288.25，前復權應為 2,327.36。n_day_low 在 9/2 之後觸發 10 次，前復權為 0 次。

自選股的影響：
- 00878 在 8/18：app -4.06%，以參考價計算 -1.10%，並誤觸 ma20_breakdown。同日的 ma5_breakdown 在前復權下也會觸發，不是誤觸。
- 2379 在 9/8：app -5.08%，以參考價計算 -1.70%。同日的 ma5_breakdown 在前復權下也會觸發，不是缺口造成的誤觸。
- 3545 在 8/4：app -0.40%，以參考價計算 +1.26%。
- 6196 在 6/24：app -0.18%，以參考價計算 +1.02%。
- 2454 在 7/7：app -2.30%，以參考價計算 -1.72%。
- 0050 在 7/21：app +3.33%，以參考價計算 +3.96%。
- 3034（finding 未提）：除權息日 7/10 全市場沒有資料，7/13 收在 TWT49U 的跌停價 467.5，app 卻顯示 -13.75%，超過 10% 漲跌幅限制；以參考價計算應為 -9.92%。

官方口徑：交易所 STOCK_DAY 在除權息日的漲跌欄標為「X0.00（不比價）」，漲跌停價以除權息參考價為基準，券商看盤軟體的平盤價也是參考價。
    - 理由：我用官方 TWT49U 與本地 parquet 各自重算，finding 的核心說法與全部數字都完全重現。

1) 程式碼：
- enriched_refresh.py:95 固定傳入 factors=pl.DataFrame()。
- hybrid_provider.py:144-152 的 docstring 寫明 "Return empty adjustment factors (Phase 2 records raw price semantics)"，也就是固定回傳空表。
- pipeline.py:1023-1024 只有 factors 非空時才會前復權。
- pipeline.py:503-504 的 change_pct 是 close/close.shift(1)-1。
- 這與 CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」牴觸。
- 已存檔的 enriched 自 2026-04-01 起共 275,671 列，close≠raw_close 的列數是 0。

2) 事件統計：TWT49U 有 994 個事件，其中 956 個對得上 enriched，對不上的 38 個是特別股、REITs，以及 7/10 當天全市場都沒有資料的事件。這 956 個事件的 prev_close 全部等於官方的「除權息前收盤價」（956/956），可見分母確實是除權息前收盤，不是參考價。
- app change_pct 減掉以參考價計算的漲跌：min -66.348、中位數 -2.824、max +1.453 個百分點，與 finding 一致。
- app 為負、以參考價計算卻為上漲的事件有 350 個，一致。
- 除權息日觸發 ma20_breakdown 的有 296 個，其中以參考價計算為上漲的有 131 個，一致。

3) 6669：
- 9/2 的 change_pct 為 -0.665385，一致。
- momentum_20d 從 9/1 的 +0.257 變成 9/2 的 -0.577，一致。
- rsi_14 從 77.44 變成 26.41，之後 9/3～9/23 一直停在 25.3～26.9。
- 9/23 的 ma20 為 3288.25，收盤 2105，一致。
- signal_n_day_low 在 9/2 之後觸發 10 次。

4) 自選股：以下皆為「app 漲跌／以參考價計算的漲跌」，全部重現。
- 2379 9/8：-5.075%／-1.705%
- 00878 8/18：-4.059%／-1.10%
- 3545 8/4：-0.399%／+1.258%
- 6196 6/24：-0.185%／+1.02%
- 2330 9/16：-0.21%／+0.085%
- 2454 7/7：-2.30%／-1.72%
- 0050 7/21：+3.33%／+3.96%

5) 官方口徑：
- STOCK_DAY 在 2379 9/8、00878 8/18、2330 9/16、3545 8/4 的漲跌欄都是 "X0.00"，說明為「X表示…不比價」。
- TWT49U 的 formula 欄與 finding 引用的公式相同。
- 漲跌停以參考價為基準：2379 參考價 704，漲停 774 = 704×1.1 向下取檔位，跌停 634 = 704×0.9 向上取檔位。
- 網路來源（XQ 說明頁、Win 投資）也說明除權息日的平盤價是參考價。

另外我自己做了反事實驗證：用 TWT49U 建出 ex_factor = 前收／參考價，再以純計算函式 compute_enriched(factors=...) 重算全市場。
- 不帶 factors 的重算結果與已存檔 enriched 一致：對照 106,748 列，change_pct 與三種訊號的差異都是 0。
- 帶 factors 後，除權息日的 change_pct 與 close/參考價-1 的最大差為 3.3e-16，證明 finding 的修法方向可行。

以下是需要修正或補充的細節，不影響主結論：
(a) 2379 在 9/8 的 signal_ma5_breakdown，以前復權價重算後仍會觸發，所以不是缺口造成的誤觸。00878 在 8/18 的 ma5_breakdown 在前復權下也會觸發，只有 ma20_breakdown 是誤觸。
(b) finding 用「131 個上漲日」衡量誤觸，低估了影響。以前復權反事實計算：
- 除權息日的 296 個 ma20_breakdown 中，有 222 個會消失。
- ma5_breakdown 335 個中消失 206 個。
- n_day_low 168 個中消失 132 個。
- boll_breakdown_lower 257 個中消失 219 個。
- 反過來也漏報了訊號：除權息日的 ma5_breakout 少報 125 個、n_day_high 少報 65 個。
- 2026-06-01 以後全期間，只有原始價觸發的 n_day_low 有 1,389 列，只有前復權才觸發的 n_day_high 有 1,011 列。
(c) 漏掉自選股 3034：它的除權息日是 7/10，但全市場都沒有這天的資料（推測是休市）。7/13 收在 467.5，剛好等於 TWT49U 以參考價 519 算出的跌停價，app 卻顯示 -13.75%，超過台股 10% 的漲跌幅限制；以參考價計算應為 -9.92%。
(d)「與交易所顯示一致」一句不精確：交易所 STOCK_DAY 在除權息日是標「不比價」，並不是顯示以參考價計算的漲跌。以參考價為基準的是漲跌停價，以及券商看盤軟體的平盤價。

嚴重度維持 P1：這屬於錯誤的金融口徑，而且直接影響自選股的漲跌幅與訊號。至於 K 線卡片在 UI 上實際顯示的數字，我沒有啟動前端驗證，只從 docs/code-review-2026-09-22.md A2「歷史列的 change_pct 本來就等於兩列相除」推論卡片會用這個值，屬於推測。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95：enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/taiwan/providers/hybrid_provider.py:151："Return empty adjustment factors (Phase 2 records raw price semantics)."，函式定義在 :144
      - backend/app/indicators/pipeline.py:1023-1024（只有 factors 非空才呼叫 _apply_adj_factor）；:504（change_pct = close/close.shift(1)-1）；:656（signal_n_day_low = close <= low_60d）
      - CONTRIBUTING.md:100：「enriched 的 `open/high/low/close` 為前復權價格。」
      - 腳本 AUDIT_SCRATCH/work/verify-f2exdiv/v1_events.py 的輸出：TWT49U 共 994 個事件，對得上 enriched 的 956 個；close!=raw_close 的列數為 0（2026-04-01 起共 275,671 列）；prev_close 等於官方除權息前收盤價的比例為 956/956；diff_pp min -66.347639、median -2.823672、max 1.452962；app<0 且以參考價計算>0 的有 350 個；除權息日觸發 ma20_bd 296 個，其中以參考價計算上漲的 131 個；ma5_bd 335 個；n_day_low 168 個
      - v1 自選股輸出（app%／以參考價計算%）：2379 9/8 為 -5.075／-1.705，ma5_bd=true；00878 8/18 為 -4.059／-1.10，ma5_bd 與 ma20_bd 皆為 true；3545 8/4 為 -0.399／+1.258；6196 6/24 為 -0.185／+1.02；2330 9/16 為 -0.21／+0.085；2454 7/7 為 -2.303／-1.719；0050 7/21 為 +3.327／+3.955；6669 9/2 為 -66.538／-0.191
      - v2_6669.py 輸出：6669 在 9/1 的 momentum_20d 0.257、rsi_14 77.44；9/2 的 change_pct -0.665385、momentum_20d -0.577、rsi_14 26.41；9/3～9/23 的 rsi_14 介於 25.33～26.88；9/23 的 ma20 3288.25、close 2105；signal_n_day_low 觸發日為 9/2、9/3、9/8、9/9、9/11、9/14、9/16、9/17、9/22、9/23
      - v3_counterfactual.py（純函式 compute_enriched，factors 用 TWT49U 的前收／參考價）：6669 9/2 前復權後 change_pct -0.0019、momentum_20d +0.261、rsi_14 77.1、9/23 的 ma20 2327.36，9/2 之後 n_day_low 觸發 0 次；2379 9/8 前復權後 ma5_breakdown 仍為 true；00878 8/18 前復權後 ma5_bd 為 true、ma20_bd 為 false；3034 7/13 原始 -0.1375、前復權 -0.0992
      - v4_market_counterfactual.py（全市場 TWSE 共 817,886 列）：base 與已存檔 enriched 對照，change_pct 差 0、訊號不一致 0；除權息日訊號（原始觸發數／前復權觸發數／只有原始才觸發）：ma20_breakdown 296/74/222，ma5_breakdown 335/129/206，n_day_low 168/36/132，boll_breakdown_lower 257/38/219；只有前復權才觸發的：ma5_breakout 125、n_day_high 65；2026-06-01 起全期間只有原始才觸發的 n_day_low 1,389 列，只有前復權才觸發的 n_day_high 1,011 列；前復權 change_pct 與 close/參考價-1 的最大差 3.33e-16
      - 官方 STOCK_DAY 參考資料 raw/twse_stock_day_2379_202609.json：['115/09/08', …, '692.00', 'X0.00', …]，notes：「符號說明:+/-/X表示漲/跌/不比價」；csv 中 2330 9/16、3545 8/4、00878 8/18 的 change_raw 同樣是 X0.00
      - 官方 TWT49U raw json 的 formula 欄：「除權息參考價 = (除權息前收盤價-息值+現金增資認購價*現金增資配股率)/(1+無償配股率+現金增資配股率)」；2379 9/8 參考價 704.0、漲停 774.0、跌停 634.0；3034 7/10 參考價 519.0、跌停 467.5，而 app 3034 7/13 收盤 467.5、change_pct -0.137454
      - https://xshelp.xq.com.tw/XSHelp/api?a=%E5%8F%83%E8%80%83%E5%83%B9&b=ta：「當日交易的參考價。一般而言是前一交易日的收盤價，如果股票商品有除權息的話，則是除權息後的參考價。」
      - https://winvest.tw/Knowledge/Article/232（WebFetch 摘要）：「你的股票在除權或除息的這天，它的「開盤價」會先做一個調整」「這個調整後的價格，我們就叫它「參考價」」
      - 工作樹比對：git status --short 仍然只有執行前就存在的 3 個修改檔（daily_update.py、pnpm-lock.yaml、vite.config.d.ts），本次沒有寫入 repo 或 data

### [P2 → P2] F5 data_integrity 只檢查台股部署不寫入的 A 股表：看不到台股資料缺漏，還會誤報並觸發空轉的修復任務，讓開啟即時行情時回 409（確認成立）

- **主張**：scan_recent_integrity 只掃 kline_daily／kline_etf_daily／kline_index_daily，從不檢查 data/taiwan/daily 或 kline_daily_enriched，所以 F1 與 F4 都偵測不到（9/23 22:03 的啟動檢查記錄「近 7 个交易日数据完整」）。台股已不再寫入 kline_daily（最後一個分區是 9/22），因此 9/24～9/29 會對 9/23 等日期報「缺失」。repair_daily.py 的 docstring 聲稱台股不持有 KLINE_DAILY_BATCH，但 detect_capabilities 其實有授予，於是 launch_integrity_repair 會建立任務：任務先暫停即時行情、再立刻以「A 股日K修正管道已下線」失敗；同時 update_realtime_quotes 會回 409，訊息寫「已自動創建修復任務，完成後即可開啟實時行情」
- **正確的定義或數值**：完整性檢查應涵蓋台股正本（TaiwanDailyStore 各交易所的列數、enriched 分區）；不適用的 A 股表不應觸發修復，也不應阻擋即時行情開關
- **app 實際行為**：真正的缺漏看不到，不存在的缺口卻會觸發空轉任務並回 409
- **對使用者的影響**：使用者目前已開啟即時行情。若在 9/24～9/29 關閉後再重新開啟，會一直收到 409，提示「修復任務」，但任務永遠修不好；每次啟動也會建立一個失敗的任務，並短暫暫停即時行情
- **修法建議**：台股部署改為掃 data/taiwan/daily，依交易所比對列數（可偵測 F1），並把 kline_daily 系列排除在外；或讓 detect_capabilities 不授予 KLINE_DAILY_BATCH、讓門禁在沒有可用修復時不回 409；同時修正 repair_daily.py 的 docstring
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/services/data_integrity.py:44（_DAILY_TABLES 只有 A 股三表）
  - backend/app/services/data_integrity.py:138-163（尾部缺口判定）
  - backend/app/services/data_integrity.py:247-279（能力門檻、qs.paused、run_repair_daily）
  - backend/app/tickflow/policy.py:17（台股授予 KLINE_DAILY_BATCH）
  - backend/app/services/repair_daily.py:5-8,22-31（docstring 與實際不符；函式直接回 error）
  - backend/app/api/settings.py:843-858（即時行情開關的門禁，會回 409）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 唯讀呼叫 scan_recent_integrity(真實 data 目錄, today=…)：9/23 回 []；9/24 回 [('2026-09-23','kline_daily','missing')]，且在自動修復窗口內；9/25 回 9/23、9/24 缺失；9/29 超出窗口；9/30 回 []（該族分區太舊，整族略過）。detect_capabilities().has(KLINE_DAILY_BATCH) = True
  - data\backend.log:45697（9/23 22:03）「boot integrity check: 近 7 个交易日数据完整」；此時台股 store 的 9/16 缺整個上櫃
  - data/kline_daily 只剩 9/16、17、18、22 四個分區，各 6～9 列（c5_kline_daily.py）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：scan_recent_integrity 只掃 A 股三族表（data_integrity.py:44），從不檢查 data/taiwan/daily 或 kline_daily_enriched。因此 9/16 和 9/23 整個上櫃缺漏都偵測不到，9/23 22:03 的啟動檢查還記錄「近 7 个交易日数据完整」。台股部署因為 _live_write_allowed 加上 _records_datable_today 兩道門檻，實際上已不再寫 kline_daily（最後分區是 9/22）。所以 9/24～9/28 這段期間，檢查會把 9/23（以及之後的工作日，包括休市的 9/25、9/28）報成「缺失」，而且落在自動修復窗口內。policy.py:17 其實有授予 KLINE_DAILY_BATCH，與 repair_daily.py docstring 的說法相反，於是 launch_integrity_repair 會建立任務：先暫停即時行情，再立刻以「A 股日K修正管道已下线」失敗。9/24 00:45～01:20 已實際留下 5 筆失敗任務。在這段期間把即時行情關掉再打開，會一直收到 409「已自动创建修复任务…」。9/29 起最早壞日已超出 5 天窗口，不再回 409；9/30 起整族被略過，這個檢查對台股從此永久空轉。以使用者情境來看，409 只可能影響 9/24 這一個交易日，而且必須是關掉再打開才會遇到；每次啟動則都會產生一筆失敗任務。
    - 理由：我逐一重讀了引用的程式碼（行號與現況一致），也用唯讀方式重跑了掃描，finding 的核心機制全部成立。
(1) data_integrity.py:44 的 _DAILY_TABLES 只有 kline_daily、kline_etf_daily、kline_index_daily 三張表。scan_recent_integrity（:138-163）只看這三張表的分區有沒有缺、是不是盤中快照，完全不讀 data/taiwan/daily 或 kline_daily_enriched。
(2) 台股部署實際上已經不會再寫 kline_daily。repository.py:1396-1410 的 _live_write_allowed 只准寫「官方已公布資料的日期」；官方日線要到 16:30 才入庫（daily_pipeline.py:178-181）。收盤後 MIS 報價會被當成過期而剔除（hybrid_provider.py:182），改走官方來源，狀態是 official，接著被 _records_datable_today 濾掉（quote_service.py:1056-1070）。實證：9/23 盤中有數百次行情刷新，22:02:48 也做過一次最終同步（backend.log:45621），kline_daily 仍然沒有 9/23 分區。最後一個分區是 9/22 19:16，寫入時間早於 f43c7bd（9/22 19:36，加入了這道過濾）。
(3) 我唯讀重跑 scan_recent_integrity 的結果：9/23 回傳 []；9/24 回傳 9/23 missing，且落在自動修復窗口內；9/25、9/28 同樣是 missing 並落在窗口內；9/29 仍有 issues，但超出窗口；9/30 回傳 []（整族分區太舊，直接略過）。
(4) policy.py:17 確實授予 KLINE_DAILY_BATCH，實跑 detect_capabilities().has(...) 得到 True，因此 repair_daily.py:5-8 docstring 說「Taiwan 部署不會持有該能力」是錯的。
(5) launch_integrity_repair 會先暫停行情、再呼叫 run_repair_daily，後者直接回傳 error。這在正式環境已經發生：backend.log:45996-45999 依序記錄「缺失 → 行情輪詢已臨時暫停 → 任務啟動 → A-share daily pipeline has been removed → 行情輪詢已恢復」。data/job_store 裡已有 5 筆 status=failed、error 為「A 股日K修正管道已下线」的任務（9/24 00:45～01:20）。
(6) settings.py:834-858：開啟即時行情時，只要 issues 落在窗口內就一定建立任務並回 409；任務秒失敗，下次再開又會新建任務、再回 409。
(7) 真實缺漏看不到：本地 taiwan/daily 的 9/16 和 9/23 都只有 TWSE 1330 列、TPEX 0 列，enriched 也一樣。官方參考資料（tpex_trading_stock）證實 6488、8069 在 9/16、9/23 都有成交。9/23 22:03 的啟動檢查卻記錄「近 7 个交易日数据完整」（backend.log:45697）；9/24 的檢查也只報了 kline_daily 的假缺口。

需要更正或補充的地方：
(a) 會回 409 的期間是 9/24～9/28，不是 9/29。9/29 時最早壞日距今 6 天，超過 AUTO_REPAIR_MAX_LAG_DAYS=5，不會建任務也不會回 409（實跑 in_window=False）。
(b) 工商時報證實 9/25（中秋）與 9/28（教師節）休市，所以這段期間裡使用者真正會交易的只有 9/24 一天。另外 _candidate_days 用「工作日」近似交易日，會把這兩天也報成缺失；描述文字雖然會多列這兩個日期，但不影響最早壞日的判定。
(c) 這個 409 會自己消失：9/30 起 kline_daily 最新分區早於掃描窗口，整族被略過，之後這個檢查對台股等於永久空轉，也永遠不會偵測到台股缺漏。
(d) 使用者目前已經開著即時行情，所以 409 只有在「關掉再開」時才會遇到；每次啟動則都會建一個失敗任務，並讓行情暫停幾毫秒。

嚴重度：屬於「明確的功能錯誤」，包括開關門禁誤擋、空轉任務、完整性訊息誤導。影響期間短、會自己消失，不會破壞資料，也不是錯誤的金融口徑，所以維持 P2。至於 409 的用法，MDN 說明 409 表示請求與資源目前狀態衝突，而且使用者可以自行解決後重送。這裡的衝突在修復管道已下線的情況下使用者無從解決，409 的用法不當。
    - 證據：
      - backend/app/services/data_integrity.py:44 `_DAILY_TABLES = ("kline_daily", "kline_etf_daily", "kline_index_daily")`；:138-163 只掃這三族的分區（尾部缺口判定，以及 quote_ts 盤中快照判定）
      - backend/app/services/data_integrity.py:250-279 以 Cap.KLINE_DAILY_BATCH 為門檻，接著 job_store.create()、qs.paused()、run_repair_daily；:288-289 結果含 error 就把任務標為 fail
      - backend/app/tickflow/policy.py:17 `Cap.KLINE_DAILY_BATCH: CapabilityLimits(rpm=None, batch=100)`；實跑 detect_capabilities().has(Cap.KLINE_DAILY_BATCH) 得到 True
      - backend/app/services/repair_daily.py:5-8 docstring 寫「Taiwan 部署不会持有该能力, 故此函数实际不会被触发」；:29-31 直接 return {"error": "A 股日K修正管道已下线"}
      - backend/app/api/settings.py:834-858：開啟即時行情時，issues 落在窗口內就呼叫 launch_integrity_repair，再 raise HTTPException(409, "…已自动创建修复任务，完成后即可开启实时行情…")
      - backend/app/repository.py:1396-1410 _live_write_allowed 只准寫官方已公布的日期；backend/app/services/quote_service.py:1056-1070 只留 status==realtime 的列；backend/app/taiwan/providers/hybrid_provider.py:182 剔除過期報價。所以台股部署實際上不再寫 kline_daily
      - 本地唯讀計算（scratchpad\audit\work\verify_f5_def\scan.py）：9/23 回傳 []；9/24 回傳 [(9/23, kline_daily, missing)]，in_window=True；9/25、9/28 in_window=True；9/29 有 4 筆 issues 但 in_window=False；9/30 回傳 []
      - data\kline_daily 只有 9/16、17、18、22 四個分區（6/9/9/9 列，全是自選股），9/22 的 mtime 是 19:16:35；backend.log:45621-45622 顯示 9/23 22:02:48 最終同步刷新了 9 檔，但沒有產生 9/23 分區
      - data/backend.log:45996-45999（9/24 00:45:11）依序記錄：缺失 → 行情輪詢已临时暂停 → 自动修复任务启动 job=46293b8dc8 → A-share daily pipeline has been removed → 行情轮询已恢复；另有 46187、46331、46499、46582 同樣各一次
      - data/job_store/ 有 5 個 json（033ddf0a65、46293b8dc8、aacf8c81ff、cf3cdbb5c7、d2b2667224），都是 status=failed、error="A 股日K修正管道已下线"、duration_s=0.0
      - data/backend.log:45697（9/23 22:03:22）記錄「boot integrity check: 近 7 个交易日数据完整」
      - 本地計算（tw.py）：data\taiwan\daily 的 9/16 與 9/23 都只有 TWSE 1330 列、TPEX 0 列（其他日期約 1006 列）；kline_daily_enriched 的 9/16 與 9/23 同樣沒有 TPEX
      - 官方參考資料 reference\csv\tpex_trading_stock_202608_202609.csv：6488 在 2026-09-16 成交 6666 張、2026-09-23 成交 28715 張；8069 在 9/16、9/23 也都有成交，證實上櫃這兩天都有交易
      - 線上：工商時報 https://www.ctee.com.tw/news/20260917700877-430201 原文「因9月25日（周五）為中秋節暨9月28日（周一）為教師節，集中交易市場休市」「台股將從9月25日到28日，連同周六及周日，共將休市四天」
      - 線上：MDN https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/409 原文「indicates a request conflict with the current state of the target resource… so that a user might be able to resolve a conflict and resubmit the request」；本案的衝突使用者無法自行解決
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：核心敘述全部成立，只有一個時間範圍要修正，另外補充機制與證據。

時間範圍：9/24～9/29 的掃描都會回報 9/23 起「缺失」，但會觸發修復任務和 409 的只有 9/24～9/28（最早缺漏日 9/23 距今不超過 5 天）。9/29 已超出自動修復窗口，只記 log，不建任務、不回 409；9/30 起整族略過。所以 user_impact 寫的「9/24～9/29 會一直收到 409」應改成「9/24～9/28」。

機制：kline_daily 的寫入函式還在，但被 _live_write_allowed（官方當日資料要先存在）和 _records_datable_today（只收 status=realtime 的筆）同時擋住。自 f43c7bd（9/22 19:36）起實際上不再寫入：9/23 後端從盤中一路跑到 22:03，也沒有產生 9/23 分區。

補充證據：backend.log 顯示 9/24 00:45～01:20 每次重啟都建立一個失敗的修復任務，共 5 次，每次都短暫暫停即時行情。

新發現：台股正本 9/23 同樣缺整個上櫃（TWSE 1330 列、TPEX 0 列）。檢查報出 9/23「缺失」只是因為 kline_daily 沒有分區，並不是偵測到這個上櫃缺漏，而且它觸發的修復任務也補不回上櫃資料。
    - 理由：我自己寫了腳本並直接唯讀呼叫 scan_recent_integrity(真實 data 目錄, today=…)，結果跟 finding 列的數值逐一相同。backend.log 另外留有 9/24 凌晨實際發生、而且一直重複的失敗修復任務，是比 finding 更直接的證據。

1) 程式碼：
- _DAILY_TABLES 只列了 kline_daily、kline_etf_daily、kline_index_daily 三張表（data_integrity.py:44），整個掃描都沒有讀 data/taiwan/daily 或 kline_daily_enriched。
- detect_capabilities 會授予 KLINE_DAILY_BATCH（policy.py:17），我實際執行後回傳 True。repair_daily.py:5-8 的 docstring 說「Taiwan 部署不會持有該能力」，跟實際不符；run_repair_daily 直接回傳 {"error": "A 股日K修正管道已下線"}（repair_daily.py:29-31）。
- settings.py:843-858 的門禁在 is_new 時會 raise 409，訊息是「已自動創建修復任務，完成後即可開啟實時行情」。job_store.create 只重用 pending 或 running 的任務（pipeline_jobs.py:188），已失敗的任務不會被重用，所以每按一次開關就會建一個新任務、再回一次 409。

2)「台股已不再寫入 kline_daily」要補充機制。寫入函式 merge_live_daily_asset 還在（repository.py:2192-2219），但同時被兩道條件擋住：
- _live_write_allowed 要求官方 store 已有當天資料（repository.py:1396-1410），而官方 store 要 16:30 才寫入。
- _records_datable_today 只放行 status=realtime 的筆（quote_service.py:1056-1070；f43c7bd，9/22 19:36 加入）。

實測結果：9/23 盤中有 1048 筆「行情刷新」，13:29 之後就沒有任何「enriched 增量」，一路跑到 22:03 都沒產生 date=2026-09-23 分區。9/22 那個分區是 19:16:35 寫的，時間早於 f43c7bd。所以就實際效果來說，finding 這句話成立。

3) 另外做了模擬：如果真有當天分區存在，9/23 會變成內部空洞，不再回報。但以目前的程式，這種情況實際上不會發生。
    - 證據：
      - backend/app/services/data_integrity.py:44 _DAILY_TABLES = ("kline_daily", "kline_etf_daily", "kline_index_daily")；:138-163 只掃這三張表，而且只回報晚於 latest 的尾部缺口
      - backend/app/tickflow/policy.py:17 Cap.KLINE_DAILY_BATCH: CapabilityLimits(rpm=None, batch=100)；腳本輸出 has KLINE_DAILY_BATCH True
      - backend/app/services/repair_daily.py:5-8 docstring 寫「Taiwan 部署不會持有該能力」；:29-31 直接 return {"error": "A 股日K修正管道已下线"}
      - backend/app/api/settings.py:847-858 issues 落在修復窗口內，且 launch_integrity_repair 回傳 job_id 時，raise HTTPException(409, '检测到…已自动创建修复任务，完成后即可开启实时行情')；pipeline_jobs.py:188 只重用 pending/running 的任務
      - backend/app/repository.py:1396-1410 _live_write_allowed 要求 date_has_official_stock_data；quote_service.py:1056-1070 只放行 status==realtime。兩者合起來，使 kline_daily 實際上不再被寫入
      - 腳本 AUDIT_SCRATCH/work/verify-f5-integrity/repro.py（輸出 out.txt 在同目錄）。scan 結果：9/23→[]；9/24→[9/23 missing]，在窗口內=True；9/25→[9/23,9/24]，True；9/28→[9/23,9/24,9/25]，True；9/29→[9/23,9/24,9/25,9/28]，在窗口內=False；9/30→[]。與 finding 一致
      - kline_daily 分區（本地實算）：9/16 有 6 列，9/17、9/18、9/22 各 9 列，都是自選股代號；kline_etf_daily、kline_index_daily 目錄存在但沒有 date= 分區。與 finding 的「4 個分區、各 6～9 列」一致。9/22 part.parquet 的 mtime 是 2026-09-22 19:16:35，早於 f43c7bd（19:36）
      - data\backend.log:45697「2026-09-23 22:03:22 boot integrity check: 近 7 个交易日数据完整」。同一時間 taiwan/daily date=2026-09-16 只有 TWSE 1330 列、TPEX 0 列（9/15 是 TPEX 1006 列、TWSE 1330 列）；enriched 9/16 有 1319 列（9/17 為 2308 列）。官方參考 csv/tpex_trading_stock_202608_202609.csv 顯示 6488、8069 在 2026-09-16 有成交，所以 9/16 是真的缺漏
      - 新證據：data\backend.log:45996-45999（2026-09-24 00:45:11）依序記錄「2026-09-23 的数据为缺失」→「行情轮询已临时暂停」→「自动修复任务启动 job=46293b8dc8」→「repair_daily: A-share daily pipeline has been removed, skipping」→「行情轮询已恢复」。9/24 00:45～01:20 之間共 5 個這樣的失敗任務（行 45998、46186、46330、46498、46581），每次重啟都建一個
      - 新發現：taiwan/daily date=2026-09-23 也只有 TWSE 1330 列、TPEX 0 列。9/24 的掃描回報「9/23 缺失」，但依據的是 kline_daily 沒有分區，不是偵測到上櫃缺漏；而且這個修復任務也補不回上櫃資料

### [P3 → P3] F10 逐檔路徑（official_provider）的上櫃量以張×1000 近似且不含鉅額，與快照路徑口徑不同（確認成立）

- **主張**：refresh_symbols（CLI --symbols）走 TPEx tradingStock，量與金額由四捨五入後的張、千元乘以 1000 得到，且該端點不含鉅額；日常快照路徑的 dailyQuotes 是精確股數並含鉅額。兩條路徑寫進同一張表，但口徑不同
- **正確的定義或數值**：兩條路徑寫入相同口徑
- **app 實際行為**：逐檔路徑有最多 ±500 股的誤差，且缺少鉅額
- **對使用者的影響**：只有手動使用 --symbols 時才會發生；日常排程不走這條路徑
- **修法建議**：逐檔路徑的上櫃資料改用 dailyQuotes，或在寫入前標記 provenance，並禁止覆寫快照來源的列
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/official_provider.py:133-134
  - backend/app/taiwan/daily_update.py:289-291
- **線上證據**：
  - （無）
- **本地計算證據**：
  - c10：6488 在 9/23 的逐檔解析得 volume 28,715,000、amount 27,580,042,000；官方 dailyQuotes 為 28,714,838 與 27,580,042,174
  - c1：6488 在 9/01 的 store（dailyQuotes）比 tradingStock 張數×1000 多 875,310 股（鉅額）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：確認成立，數值分毫不差地重現。

refresh_symbols（CLI --symbols）走 TaiwanHybridProvider，再到 OfficialTaiwanAdapter._fetch_month。上櫃的量與額取自 TPEx tradingStock 四捨五入後的「成交張數」與「成交仟元」再乘以 1000（official_provider.py:133-134），而該端點註明不含鉅額交易。日常快照路徑用 dailyQuotes，量是精確股數並含鉅額。

實測數據如下：
- 鉅額以外的日子，誤差在 ±500 股與 ±500 元以內：6488 最大 495 股，8069 最大 456 股。
- 有鉅額的日子，缺口剛好等於鉅額成交量。6488 在 9/01 為 875,310 股，其中鉅額 875,000 股；9/15 為 687,423 股，其中鉅額 687,000 股。

finding 沒寫到的部分有兩點：
1. 兩條路徑寫進同一張沒有 provenance 欄的 TaiwanDailyStore。refresh_symbols 會把相隔 ≤3 天的缺漏日合併成一段範圍，而 write_batch 以新進資料為準（keep="last"），所以逐檔路徑實際上會覆寫已存在的快照列。例如 6488 在 9/15 會從 8,270,423 股被改寫成 7,583,000 股。
2. 這違反 docs/taiwan-data-sources.md:72-73「must retain separate provenance and must not overwrite each other」的要求。

影響：只有手動使用 --symbols 的上櫃標的會受影響。使用者自選清單全部是上市股，日常排程也不走這條路徑，因此維持 P3。
    - 理由：我自己寫腳本離線重算，沒有連網。做法是用子類別覆寫 OfficialTaiwanAdapter._json，讓它讀本地參考檔 tpex_trading_stock_{6488,8069}_2026{08,09}.json，再呼叫原本的 _fetch_month 解析。解析結果與 TPEx dailyQuotes 9/23，以及本地 parquet store 2026-08/09 的資料逐日比對。finding 的兩項數字都能分毫不差地重現。

【finding 數字與我的重算並列】
- c10（6488，9/23）：finding 寫逐檔解析 28,715,000 股、27,580,042,000 元，dailyQuotes 28,714,838 股、27,580,042,174 元。我算出的四個數字完全相同，差值為量 +162 股、額 −174 元。另外補驗 8069 同日：解析 5,797,000 對 dailyQuotes 5,796,924（+76）；金額 841,678,000 對 841,677,554（+446）。
- c1（6488，9/01）：finding 寫 store 比 tradingStock 張數×1000 多 875,310 股。我算出 dvol=875,310 股、damt=869,750,254 元，完全重現。Yahoo 新聞「9/1櫃買市場鉅額交易日成交資訊」（2026-09-02 發布）列出 6488「配對交易-單一型」，成交價 994、875,000 股、869,750,000 元。扣掉這筆鉅額後剩 310 股、254 元，落在四捨五入範圍內。
- 鉅額以外的日子：6488 共 31 天，|差| 最大 495 股；8069 共 33 天，最大 456 股。差值正負都有，符合「四捨五入到張／千元」、誤差在 ±500 以內的說法。收盤價 36/36 天全部一致。
- 其他超過 ±500 的日子：6488 有 8/20（+404,142）、8/28（+249,540）、9/15（+687,423）。其中 9/15 我向外部核對過：MoneyDJ 列出 6488 四筆鉅額，分別是 375,000、125,000、100,000 股（價格都是 810）與 87,000 股（價格 919），合計 687,000 股、565,953,000 元，扣除後殘差 423 股、−204 元。8/28 與 docs/taiwan-data-sources.md:87-99 記載的「鉅額 250,000 股、dailyQuotes 總量 11,772,540 股」一致。8069 則是 8/10（+150,417）、8/11（+179,233）、8/21（+99,828）。6488 在 8/18 差 +571 股、+129,030 元，略超過 ±500，推測是小額的股票組合型鉅額，沒有查證。

【程式路徑】
- CLI --symbols 走 daily_update.py:289-291 → refresh_symbols → TaiwanHybridProvider.get_daily，而這條路徑以 official 為主，對應 OfficialTaiwanAdapter._fetch_month，在 official_provider.py:133-134 把量與額各乘 1000。
- 未指定 symbols 時走 daily_update.py:302 → refresh_dates，也就是 snapshot 的 dailyQuotes，程式在 daily_refresh.py:137-186。
- 兩條路徑都寫進同一張 TaiwanDailyStore。這張表的欄位只有 TAIWAN_DAILY_COLUMNS（daily_store.py:33-35），沒有 source 或 provenance 欄。

【finding 沒提到、但會加重問題的面向】
1. 逐檔路徑會覆寫快照寫入的列，finding 的修正建議只把它當成預防措施。細節如下：
   - _coalesce_dates_to_ranges 會把間隔 ≤3 天的缺漏日合併成一段（daily_refresh.py:74）。我實測 [9/14, 9/17] 會合併成 (9/14, 9/17)，這段範圍包含已存在的 9/15、9/16。
   - _fetch_month 抓整個月再依範圍過濾。
   - write_batch 合併時以新進資料為準（daily_store.py:132,138，keep="last"）。
   - 結果是已存在、含鉅額的快照列會被改寫成 ×1000 且不含鉅額的值。例如 6488 在 9/15 會從 8,270,423 股變成 7,583,000 股。
2. docs/taiwan-data-sources.md:70-107 早已記載這兩個資料集的口徑差異，並要求兩者「must retain separate provenance and must not overwrite each other」。但 store 丟掉了 source，也允許覆寫，實作違反了文件自己的要求。同一份文件還寫 tradingStock 是「canonical source for indicators and backtests」，這與目前每日排程實際採用 dailyQuotes 的做法不符。

【嚴重度】
維持 P3。使用者自選清單 9 檔全部是 .TWSE，不涉及 TPEx 逐檔路徑，而且日常排程不走 --symbols。detail_service.py:280-283 在 store 沒有資料時會以 hybrid get_daily 作為顯示用的後備，同樣是 tradingStock 口徑，但只顯示、不寫入。

【範圍外但值得另案追查（推測，未深入）】
- 以 16:30 排程寫入的 TWSE 列（9/16 起）與事後的 STOCK_DAY／MI_INDEX 比對，380 列中有 16 列量少了整張數。例如 2330 在 9/23，store 是 21,978,873 股，官方是 22,817,873 股，差 −839,000 股。
- 9/16 與 9/23 兩個分區完全沒有 TPEx 列。

【工作樹】
驗證前後 git status 相同，只有原本就在的 3 個修改檔。所有腳本都寫在 scratchpad 的 audit/work/verify-f10/ 底下。
    - 證據：
      - backend/app/taiwan/providers/official_provider.py:133-134：TPEx 分支的 volume 與 amount 各乘 1000（非 TWSE 時 volume * 1000、amount * 1000）
      - reference/raw/tpex_trading_stock_6488_202609.json 的 fields 為 ["日 期","成交張數","成交仟元",...]，notes 原文："以上資料不含上櫃股票鉅額交易"
      - 腳本 verify-f10/verify.py 的輸出：6488 9/23 解析得 vol 28715000、amt 27580042000；dailyQuotes 為 vol 28714838、amt 27580042174；差值 vol +162、amt −174。8069 解析 5797000 對 5796924；金額 841678000 對 841677554
      - 腳本 verify-f10/verify.py，比對 store 與 tradingStock×1000：6488 9/01 dvol=875310、damt=869750254；9/15 dvol=687423、damt=565952796；8/20 dvol=404142；8/28 dvol=249540。8069 8/10 dvol=150417、8/11 dvol=179233、8/21 dvol=99828。鉅額以外的日子 |dvol| 最大值：6488 為 495（31 天）、8069 為 456（33 天）；收盤價 0 天不一致
      - https://tw.stock.yahoo.com/news/9-1櫃買市場鉅額交易日成交資訊-004200805.html（2026-09-02）：6488 環球晶｜配對交易-單一型｜994｜875,000 股｜869,750,000 元。875,310−875,000=310 股、869,750,254−869,750,000=254 元，在四捨五入範圍內
      - https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=9f62a0a8-7120-42e2-960e-0b7b4043bdf5（2026-09-16，9/15 櫃買鉅額）：6488 四筆，375,000 股、125,000 股、100,000 股（均價 810）與 87,000 股（價 919），合計 687,000 股、565,953,000 元；store 差值 687,423 股與 565,952,796 元扣除後殘差 423 股、−204 元
      - docs/taiwan-data-sources.md:87-99：6488 在 2026-08-28，non-block 合計 11,522,540 股，tradingStock 記為 11,523×1,000 股，鉅額 250,000 股，dailyQuotes 總量 11,772,540 股；store 同日為 11,772,540 股（1.177254e7）
      - docs/taiwan-data-sources.md:72-73 原文："They must retain separate provenance and must not overwrite each other"。但 backend/app/taiwan/daily_store.py:33-35 的 TAIWAN_DAILY_COLUMNS 沒有 source 欄
      - backend/app/taiwan/daily_update.py:289-291：有 daily_symbols 時走 refresh_symbols；:302 預設走 refresh_dates，也就是 snapshot dailyQuotes（backend/app/taiwan/daily_refresh.py:137-186）
      - backend/app/taiwan/daily_refresh.py:74 以 (d - r_end).days <= 3 合併缺漏日；實測 _coalesce_dates_to_ranges([2026-09-14, 2026-09-17]) 得到 [(2026-09-14, 2026-09-17)]，範圍包含已存在的 9/15 與 9/16
      - backend/app/taiwan/daily_store.py:132,138：先 pl.concat([existing, df])，再 unique(keep="last")，因此新進的逐檔列會覆寫已存在的快照列
      - backend/app/taiwan/detail_service.py:280-283：store 沒有資料時以 hybrid_provider.get_daily 作為顯示後備，同為 tradingStock 口徑，只顯示、不寫入
      - 範圍外：verify-f10/twse_cmp.py 比對 TWSE STOCK_DAY 與 store，380 列中 16 列不一致，全部落在 9/16 以後；例如 2330 9/23，store 為 21,978,873 股，STOCK_DAY 與 MI_INDEX 都是 22,817,873 股。store_meta.py 顯示 9/16 與 9/23 分區的 TPEx 列數為 0

### [P2 → P2] F3 除權息日的漲跌停價以除權息前收盤計算，而不是除權息參考價，造成漲停漏判與跌停誤判（部分成立）

- **主張**：_add_limit_signals 以 prev_close（等於除權息前原始收盤）乘以 1±limit_pct 算漲跌停價。官方在除權息日是以除權息參考價為基準（TWT49U 的 limit_up／limit_down 欄可證）。同一套取整公式如果套用到參考價，能重現大部分官方價格，所以只是基準價錯了
- **正確的定義或數值**：除權息日的漲跌停價以官方除權息參考價計算
- **app 實際行為**：以除權息前收盤計算，漲停價與跌停價都偏高
- **對使用者的影響**：監控規則目前為 0 條，只影響選股的訊號條件與日後的漲跌停類規則；自選股在本期間沒有命中這類誤判。口徑錯誤但影響面窄，因此評為 P2
- **修法建議**：_add_limit_signals 的 ref 在除權息日改用官方參考價（可以與 F2 共用同一份 adj_factor 或除權息表），非除權息日維持 prev_close
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:169-175（ref = prev_close）
  - backend/app/taiwan/enriched_refresh.py:181-189（四個漲跌停訊號）
  - CONTRIBUTING.md:102（漲跌停必須基於原始價及對應交易日規則判斷）
- **線上證據**：
  - 官方 TWT49U 9/08 的 2379：除權息前收盤 729、參考價 704、漲停 774、跌停 634（= 704×1.1、704×0.9 取整）
  - WebSearch 摘要（引用證交所投資人知識網《股票除權除息參考價計算說明》，https://investoredu.twse.com.tw/FileSystem/eBook/12_股票除權除息參考價計算說明.pdf，依規定未直連）：「除權息交易日漲停價格＝77.94×(1＋0.1)＝85.73；除權息交易日跌停價格＝77.94×(1－0.1)＝70.14」
- **本地計算證據**：
  - c7_exdiv.py：956 個事件中，app 的漲停價與官方不同 937 個、跌停價不同 951 個（例：2379 9/8 app 為 801／657，官方為 774／634）
  - 依 app 公式重算全部事件：22 個官方收漲停的除權息日 app 不會標漲停（例：6834 6/11 收 76.5＝官方漲停，app 漲停價 77.0），52 個非跌停日被標為跌停（例：2548 6/2 收 104.5，官方跌停 103.5，app 跌停價 110.5）
  - enriched 已存的訊號（9/10 起才有此欄，57 個事件）中有 4 筆誤判跌停：5906 9/10、6924 9/15、1442 9/23、2542 9/23
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_add_limit_signals（enriched_refresh.py:169-175）在除權息日仍以除權息前的原始收盤（prev_close）乘上 1±10% 算漲跌停價。依臺灣證券交易所營業細則第 67 條，除權息交易開始日要以「前一日收盤價減除股息紅利或權利價值」為基準。因此 app 在除權息日的漲跌停價都偏高：TWSE 876 個有限制的事件中，漲停價錯 857 筆、跌停價錯 871 筆，造成漏標漲停 22 筆、誤標跌停 52 筆；已存的 enriched（9/10 起）中有 4 筆誤標跌停。正確基準要分兩種情況。純除息或無償配股時，用除權息參考價。遇到現金增資除權時，漲停以只減除股息與無償配股的「減除股利參考價」為基準，跌停以完整的除權參考價為基準。實作上漲停取 max(除權息參考價, 減除股利參考價)、跌停取 min(兩者)，在 TWT49U 可 876/876 全部重現。只換成「除權息參考價」仍會錯 22 筆現增除權日的漲停價。影響限於除權息日，讀取端包括選股訊號、regime 的漲跌停家數、回測的一字板判斷；自選股本期間沒有被誤判，監控規則 0 條，因此維持 P2。TPEx 走同一條程式路徑，推測也受影響，但沒有官方資料可驗證。
    - 理由：finding 的核心主張成立：在除權息日，app 仍以除權息前的原始收盤價當基準，計算漲跌停價。證據如下：
(1) enriched_refresh.py:95 呼叫 compute_enriched 時沒有帶除權息因子。pipeline.py:388 的 prev_close 是前一筆原始收盤（close.shift(1)）。enriched_refresh.py:169-175 就用這個 prev_close 乘上 1±limit_pct 算漲跌停價。實際讀資料：2379 在 9/08 的 prev_close 是 729，等於官方的除權息前收盤。
(2) 權威規則：臺灣證券交易所營業細則第 67 條。來源是 selaw.com.tw 的證券暨期貨法令判解查詢系統，與 finding 引用的投資人知識網是不同來源。第 2 項規定「除息交易開始日升降幅度之計算，以前一日收盤價格減除股息及紅利金額為計算之基準」。第 3 項規定盈餘或資本公積轉增資時，以前一日收盤價減除權利價值為基準。現金增資認購價較低時，「漲幅以前一日收盤價格為計算之基準，跌幅以前一日收盤價格減除現金增資發行新股權利價值後為計算之基準」。
(3) 本地重跑：直接呼叫 app 的 _add_limit_signals，只把 _limit_profile 改成讀本地證券主檔快取，不連網、不寫檔。TWT49U 共 994 筆，能對上日K的有 956 筆，其中 876 筆有漲跌幅限制，876 筆的 prev_close 全部等於官方的除權息前收盤。app 算出的漲停價有 857 筆與官方不同，跌停價有 871 筆不同。官方收漲停的 23 筆裡，app 漏標 22 筆；app 誤標跌停 52 筆。已存的 enriched 從 9/10 起才有這兩欄，其中 57 個事件裡有 4 筆誤標跌停，與 finding 列的完全相同：5906 9/10、6924 9/15、1442 9/23、2542 9/23。
需要修正三處：
(a) 正確基準不是單一的「除權息參考價」。只用除權息參考價，漲停價只重現 854/876、跌停價 875/876。改成漲停用 max(除權息參考價, 減除股利參考價)、跌停用 min(兩者)，876/876 全部吻合，這與第 67 條的現金增資規定一致。22 筆不合的都是「權」或「權息」事件，例如 6225 8/18：官方漲停 48.8＝44.4×1.1，跌停 27.05＝30.04×0.9。所以 fix_suggestion 照原文實作，現增除權日的漲停價仍會錯。
(b) finding 說「956 個事件中漲停價不同 937 個、跌停價不同 951 個」，分母包含 80 筆沒有漲跌幅限制、app 根本不計算的標的。以 876 筆有限制的事件算，是 857 與 871 筆。結論不變。
(c) 影響面：除了選股，backtest/engine.py:1345-1346（一字板封板時禁止買賣）、backtest/matrix.py:595-596、regime_builder.py:224-233（每日漲跌停家數）、watchlist.py:268 也都讀這兩欄。不過回測只在 O=H=L=C 時才用到，data/backtest_results 目前是空的；自選股的 6 個除權息事件（0050 7/21、00878 8/18、2379 9/08、2454 7/07、3545 8/04、6196 6/24）都沒有誤判。使用者目前監控規則 0 條，影響窄，維持 P2 合理；若嚴格照「錯誤金融口徑」也可主張 P1，但錯誤只出現在約 0.4% 的個股交易日，所以不上調。
TPEx 走同一條程式路徑，應該也受影響，但參考資料沒有 TPEx 的除權息表，屬推測。
附帶發現（範圍外）：3034 在 2026-07-10 除息日，本地日K完全沒有資料。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 compute_enriched(raw, factors=pl.DataFrame(), ...)：不套用除權息因子
      - backend/app/indicators/pipeline.py:388 prev_close = pl.col("close").shift(1).over("symbol")：前一筆原始收盤
      - backend/app/taiwan/enriched_refresh.py:169-175 ref = pl.col("prev_close")，up_raw/down_raw = ref*(1±limit_pct)，再依檔位取整
      - backend/app/taiwan/enriched_refresh.py:181-189 四個漲跌停訊號都用 _limit_up/_limit_down 比較
      - CONTRIBUTING.md:102「漲停、跌停…必須基於原始價及對應交易日規則判斷」
      - 線上（與 finding 不同來源）：https://www.selaw.com.tw/Chinese/LawDetail/LawArticleReference?lawDataid=3636777，臺灣證券交易所營業細則第67條（108年9月10日修正）。第2項：「除息交易開始日升降幅度之計算，以前一日收盤價格減除股息及紅利金額為計算之基準。」第3項第1款：「遇上市公司盈餘或資本公積轉增資時…以前一日收盤價格減除權利價值後為計算之基準。」現金增資：「漲幅以前一日收盤價格為計算之基準，跌幅以前一日收盤價格減除現金增資發行新股權利價值後為計算之基準」
      - 官方參考資料 reference/csv/twse_twt49u_20260601_20260923.csv:927：2026-09-08 2379 除權息前收盤 729.0、參考價 704.0、漲停 774.0、跌停 634.0；app 由 prev_close 729 算出 801/657
      - 本地計算：uv run python ...\work\verify_f3_def\v2.py（呼叫實際的 _add_limit_signals）→ TWT49U 994 筆，對上日K 956 筆，有限制 876 筆；prev_close==除權息前收盤 876 筆；app 漲停價≠官方 857 筆，跌停價≠官方 871 筆；官方收漲停 23 筆，app 漏標 22 筆；app 誤標跌停 52 筆；炸板有 9 筆不一致，跌停打開有 34 筆不一致
      - 本地計算 v2.py：已存 enriched 從 2026-09-10 到 09-23 有訊號欄，57 個事件；誤標跌停 [5906 9/10 收37.35 官方跌停35.45, 6924 9/15 收128.0 官方125.5, 1442 9/23 收27.8 官方25.9, 2542 9/23 收39.55 官方37.35]；沒有漏標漲停，也沒有誤標漲停
      - 本地計算 v2/v4.py：以除權息參考價為基準，漲停重現 854/876、跌停重現 875/876；改成漲停以 max(除權息參考價, 減除股利參考價)、跌停以 min(兩者) 為基準，876/876 全部吻合。22 筆不合的都是「權」或「權息」事件，例：6225 2026-08-18 除權息前收盤 44.4、參考價 30.04、官方漲停 48.8(=44.4×1.1)、跌停 27.05(=30.04×0.9)
      - 下游讀取端：backend/app/backtest/engine.py:1345-1346（一字板時才阻擋買賣）、app\backtest\matrix.py:595-596、app\services\regime_builder.py:224-233（漲跌停家數）、app\api\watchlist.py:268；data\backtest_results 目前 0 個檔
      - 自選股：TWT49U 內 6 個除權息事件（0050 7/21、00878 8/18、2379 9/08、2454 7/07、3545 8/04、6196 6/24），app 與官方的訊號都是 False，沒有誤判；3034 7/10 除息日本地日K缺資料（範圍外）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：_add_limit_signals（enriched_refresh.py:169-189）在除權息日以除權息前的原始收盤（prev_close）計算漲跌停價，沒有依證交所營業細則第 67 條換成除權息日的基準價。

2026-06-01～09-23 上市除權息事件中，app 有算出漲跌停價的 876 筆裡，漲停價有 857 筆、跌停價有 871 筆跟官方 TWT49U 不同，幾乎都偏高。結果是：
- 22 筆官方收漲停的事件沒被標成漲停；
- 52 筆非跌停日被標成跌停；
- 另外炸板漏判 9 筆、跌停打開誤判 26 筆；
- 已存的 enriched（9/10 起）中，5906 9/10、6924 9/15、1442 9/23、2542 9/23 共 4 筆誤判跌停。

finding 的 937／951 是把 80 筆 app 本來就沒有 limit_pct 的事件也算進去。其中 62 筆是官方無漲跌幅，app 不算是對的；18 筆是主動式 ETF 缺 limit_pct，屬另一個問題。

正確的基準是分類別的：
- 純除息日以除息參考價計算：785/785 完全重現官方；
- 現金增資除權日，發行價較低時漲幅以前一日收盤、跌幅以減除權利價值後的價格為基準。這 22 筆（權 17、權息 5）以參考價算出的漲停價跟官方不同，其中 17 筆 app 目前的漲停價反而是對的。

因此修法不應「除權息日一律改用參考價」，應該直接採用官方公告的漲跌停價（TWT49U 的 limit_up/limit_down 與櫃買對應資料），或依第 67 條分別處理息、權、權息。自選股期間內 6 筆除權息事件的漲跌停價都不同，但沒有造成訊號誤判；監控規則為 0 條，影響面窄，維持 P2。
    - 理由：我自己寫了腳本重跑：讀本地 parquet，走 app 的 compute_enriched，再呼叫真正的 TaiwanEnrichedRefreshService._add_limit_signals（證券主檔改成只從快取載入，不會連網也不會寫檔），跟官方 TWT49U 的 994 筆除權息事件逐筆比對。

核心缺陷確認成立：
- 956 筆有對到的事件，app 的 prev_close 全部等於官方的「除權息前收盤價」（956/956）。
- 22 筆漏判漲停、52 筆誤判跌停、已存 enriched 裡 57 筆事件中 4 筆誤判跌停，以及三個舉例（2379、6834、2548）的數值，都跟 finding 一字不差。

有兩處只部分成立：

(1) 「跌停價不同 951 個」要把 app 沒有 limit_pct 的 80 筆也算成不同才湊得出來。
- 在 app 有算出漲跌停價的 876 筆裡，漲停不同 857 筆、跌停不同 871 筆。
- 那 80 筆裡有 62 筆，官方本來就是無漲跌幅（9,999.95／0.01），app 不算漲跌停反而是對的。
- 另外 18 筆是主動式 ETF（代號 A 結尾）：官方有漲跌停，app 卻完全沒有 limit_pct。這是另一個問題，跟基準價無關。
- 937 這個數字同樣是 857+80。

(2) 「官方在除權息日以除權息參考價為基準」只對純除息日完全成立。
- 純除息（息）785 筆：同一套取整公式套用參考價，漲停、跌停都 100% 重現官方。
- 除權（權）35 筆裡有 17 筆、權息 56 筆裡有 5 筆，以參考價算出的漲停價跟官方不同。
- 其中 17 筆（全部是「權」類）官方漲停價正好等於 app 用除權前收盤算的值（例：3149 7/17，除權前收盤 93.5、參考價 86.68，官方漲停 102.5＝93.5×1.1 取整）。
- 這跟證交所營業細則第 67 條的規定一致：現金增資發行價格較低時，漲幅以前一日收盤價為基準，跌幅以減除新股權利價值後的價格為基準。

所以照 finding 的修法「除權息日一律改用參考價」會把這批除權日的漲停價改錯。比較好的做法是直接採用 TWT49U（及櫃買對應資料）公告的 limit_up/limit_down，或依第 67 條分別處理息、權、權息。

另外找到 finding 沒量化的兩個副作用：炸板（signal_broken_limit_up）漏判 9 筆，跌停打開（signal_limit_down_recovery）誤判 26 筆。

「漲停價與跌停價都偏高」基本成立：856/857 筆偏高，只有 1 筆偏低（1312 6/11 是負權值，app 14.5、官方 14.7）；跌停 871/871 偏高。

自選股在期間內有 6 筆除權息事件（0050 7/21、00878 8/18、2379 9/8、2454 7/7、3545 8/4、6196 6/24），漲跌停價全部不同，但收盤離漲跌停都遠，訊號沒有誤判。影響面跟 finding 說的一致，維持 P2。

備註：因為要 import app 模組，執行時可能在 backend/app 底下產生 __pycache__；repo 的 git status 前後相同（仍只有原本的 3 個 M）。
    - 證據：
      - 程式碼：backend/app/taiwan/enriched_refresh.py:169 `ref = pl.col("prev_close")`，:170-175 用 1±limit_pct 取整，:181-189 產生四個訊號；prev_close 的來源是 backend/app/indicators/pipeline.py:388 `pl.col("close").shift(1).over("symbol")`，enriched_refresh.py:93 傳入 factors=pl.DataFrame()（不做還原），所以 prev_close 就是原始的除權息前收盤
      - 腳本：AUDIT_SCRATCH/work/verify-f3exdiv/verify.py（輸出 events.csv、missed_up.csv、false_down.csv、refbase_diff.csv）；指令 `cd backend && uv run python verify.py`。我照抄的 app 取整公式跟真正的 _add_limit_signals 比對，訊號不一致 0／0
      - 數據並列（finding → 本次重算）：對到的事件 956 → 956；prev_close＝官方除權息前收盤 → 956/956；漲停價不同 937 → 937（但其中 80 筆是 app 沒有 limit_pct；有算出漲跌停價的 876 筆中實為 857）；跌停價不同 951 → 871（876 筆中）；要加上 80 筆無 limit_pct 才等於 951
      - 漏判漲停 22 → 22（官方收在漲停價的共 23 筆，app 只抓到 1 筆）；誤判跌停 52 → 52；app 誤判漲停 0、漏判跌停 0；finding 沒提的：炸板漏判 9、跌停打開誤判 26
      - 舉例重算：2379 9/8 app 801/657 對官方 774/634；6834 6/11 收盤 76.5＝官方漲停，app 漲停 77.0；2548 6/2 收盤 104.5，官方跌停 103.5，app 跌停 110.5、signal_limit_down=True。三筆都跟 finding 相符
      - 已存 enriched：有 limit 欄的分區是 2026-09-10～09-23；對到事件 57 → 57；誤判跌停 4 筆 → 4 筆：5906 9/10（收 37.35，官方跌停 35.45）、6924 9/15（128.0／125.5）、1442 9/23（27.8／25.9）、2542 9/23（39.55／37.35）；漏判漲停 0
      - 以參考價重算、依類別分：息 785 筆，漲停／跌停不一致 0／0；權 35 筆，漲停不一致 17、跌停不一致 1；權息 56 筆，漲停不一致 5、跌停不一致 0。876 筆合計漲停 854/876、跌停 875/876 重現官方
      - 除權日的反例（官方漲停＝用除權前收盤算）：3149 7/17 除權前收盤 93.5、參考價 86.68，官方漲停 102.5、跌停 78.1（＝86.68×0.9 取整）；6225 8/18 除權前收盤 44.4、參考價 30.04，官方漲停 48.8、跌停 27.05；4764 7/24 除權前收盤 309.5、參考價 304.28，官方漲停 340.0。以上都是 TWT49U CSV 的欄位值
      - 線上來源：https://www.selaw.com.tw/Chinese/LawDetail/LawArticleReference?lawDataid=3636777（臺灣證券交易所營業細則第 67 條），WebFetch 摘錄：「除息交易開始日升降幅度之計算，以前一日收盤價格減除股息及紅利金額為計算之基準。」現金增資發行價格較低時：「漲幅以前一日收盤價格為計算之基準，跌幅以前一日收盤價格減除現金增資發行新股權利價值後為計算之基準。」
      - 沒有 limit_pct 的 80 筆：官方無漲跌幅 62 筆（app 不算是正確的）；主動式 ETF 18 筆，官方有漲跌停（例：00981A 9/16 官方 31.05／25.41、00400A 9/07 官方 16.55／13.55），app 沒有 limit_pct。另有 2059 8/27，app 有 limit_pct=0.1，但官方為無漲跌幅
      - 自選股除權息事件 6 筆（0050 7/21、00878 8/18、2379 9/8、2454 7/7、3545 8/4、6196 6/24）：漲跌停價全部不同，但 signal_limit_up／signal_limit_down 全為 False，收盤離漲跌停都遠，沒有誤判

### [P2 → P2] F4 16:30 當日抓取的上市成交量／金額偏低（缺少稍後才併入的量），之後永遠不會重抓（部分成立）

- **主張**：排程在 16:30 抓 MI_INDEX（daily_update 的截止時間是 16:00），但鉅額交易開放到 17:00，官方數字事後還會增加。refresh_dates 只要該日已有分區就不再抓，所以當日抓取的量與金額永久低於官方終值。開高低收不受影響
- **正確的定義或數值**：落盤的量與金額等於官方終值；當日的初值應在官方定稿後重抓覆蓋
- **app 實際行為**：當日抓取的分區永久保留偏低的初值
- **對使用者的影響**：成交量、均量、量比、放量訊號、換手率、成交金額排序在當日抓取的交易日偏低。自選的 2454 連 5 天偏低 0.08%～4.19%，6196 在 9/17 少了 17%
- **修法建議**：21:40 那班（或隔日早上）對最近 1～2 個交易日強制重抓 MI_INDEX 與 dailyQuotes（write_batch 本來就是 merge 且 keep=last，會自然覆蓋）；或把日線排程延到 17:30 之後
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/jobs/daily_pipeline.py:178-180（CronTrigger 16:30）
  - backend/app/taiwan/daily_update.py:38-40（DEFAULT_POST_CLOSE_HOUR=16）
  - backend/app/taiwan/daily_refresh.py:167-170（已有日期就跳過）
- **線上證據**：
  - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm：鉅額交易「逐筆交易時段為 9:00至17:00」「配對交易時段為 8:00至8:30、9:00至17:00」
  - WebSearch 摘要 https://www.twse.com.tw/zh/products/system/trading.html：逐筆交易 9:00～17:00（依規定未直連）
  - manifest：STOCK_DAY／MI_INDEX 的統計含一般、零股、盤後定價、鉅額交易
- **本地計算證據**：
  - c4：9/23 上市 1,330 檔中有 23 檔 store 量偏低（0 檔偏高），其中 22 檔的差額是整張數。總量少 12,235,374 股，金額少 3,926,807,712 元。例：6464 台數科 2,300 股，官方 302,300 股（-99.2%）；1109 -89.2%；2376 技嘉 -33.1%；2330 -3.68%
  - c1：自選股的 2454 在 9/16、17、18、22、23 五個當日抓取日全部偏低（最大 9/18 -4.19%）；6196 9/17 -16.95%；3034 9/17 -1.46%；2379 9/18 -1.25%；3545 9/18 -0.63%
  - c2 分區修改時間：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30 都是當日抓取且都偏低；8/03～9/15（9/16 11:21 回補）與 9/21（9/22 10:55 抓）這些隔日抓取的，在 10 檔上都與官方完全一致
  - enriched 的 turnover_rate 沿用偏低的量：2330 9/23 為 0.084755%，用官方量算是 0.087990%
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：當日抓取的上市分區（排程 16:30；手動或補跑在 16:00 之後也算）量與金額偏低，因為那時的 MI_INDEX 尚未併入當日的鉅額交易（配對交易，包含股票組合）。run_update 只補 d_max 之後的日期，refresh_dates 會跳過已有的分區，所以 21:40 與之後的排程都不會重抓，偏低的初值會永久保留；開高低收不受影響。9/18 在 17:03 抓取仍偏低 9%（2330），可見 17:00 收盤後官方還有發布延遲，「延到 17:30」沒有證據足以解決。修法應在 21:40 那班或隔日強制重抓最近 1～2 個交易日（隔日抓取已證實與官方一致）。同一個根因還造成 9/16、9/23 兩天的 TPEx 抓取失敗後，上櫃資料永久缺漏（這兩個分區只有上市 1,330 列），建議一併處理或另立一條 finding。
    - 理由：主張的核心成立：當日抓取的上市分區，成交量與金額低於官方終值，而且之後不會自動重抓；開高低收不受影響。我親自重跑了三個比對：(1) 用官方 STOCK_DAY 比對 10 檔在 9/10～9/23 的量。當日抓取的 9/16、9/17、9/18、9/22、9/23 都偏低，例如 2330 在 9/18 少 9.05%、6196 在 9/17 少 16.95%。隔日抓取的 9/10～9/15 與 9/21 全部一致，收盤價零差異。(2) 用 9/23 的官方 MI_INDEX 比對全部 1,330 檔上市股：23 檔偏低、0 檔偏高，量共少 12,235,374 股，金額共少 3,926,807,712 元，其中 22 檔的差額是整張數。與 finding 的數字完全相同。(3) 查了所有呼叫端：排程（daily_pipeline.py:149）與 bootstrap（bootstrap.py:397、448）都沒有傳 force。run_update 用 d_max 只補 d_max 之後的日期（daily_update.py:282），refresh_dates 遇到已有分區的日期就跳過（daily_refresh.py:150、167）。21:40 那班因此不會重抓當日，只有 CLI 的 --force 會。

官方定義：MI_INDEX「每日收盤行情」表的 notes 寫「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」。我另找了 TEJ 與 Yahoo 兩個不同來源。TEJ（2024-07-19）說鉅額配對交易為 08:00–08:30 及 09:00–17:00，逐筆交易為 09:00–17:00；股票組合的門檻是 5 種股票、合計 1,500 萬元，所以單一個股可以只有很小的量。Yahoo 列的 9/18 鉅額排行中，2330 的配對交易 1,602,000＋871,280＋423,720＋200,000＝3,097,000 股，2454 為 383,000 股、4710 元，都能解釋 store 缺少的主要部分：2330 缺 3,702,177 股，2454 缺 434,469 股、隱含價 4710。所以缺口確實主要來自鉅額交易。

需要修正的部分是成因與修法。finding 暗示問題是 16:30 時鉅額交易還在進行、到 17:00 才結束，也建議「把日線排程延到 17:30 之後」。但 9/18 那次抓取是 17:03:32～17:03:43 執行的（backend.log.1:48885-48889），已經在鉅額交易收盤之後，2330 仍然少了 9.05%。可見官方把鉅額交易併入 MI_INDEX 還有發布延遲，不只是交易時段的問題。目前沒有證據顯示 17:30 抓取就是終值；有證據的只有隔日抓取（9/16 11:21、9/22 10:55）會一致。因此修法應採用「21:40 那班或隔日強制重抓最近 1～2 個交易日」，不宜只延後排程。

嚴重度：P2（明確功能錯誤）恰當。計算口徑本身沒錯，錯在資料在時間上不完整。它不是資料破壞，但每個當日抓取的交易日都會發生，而且永久保留。在使用者情境下確實會發生：自選的 2454 連 5 天偏低；enriched 裡 2330 在 9/23 的 turnover_rate 是 0.084755%，用官方量算應為 0.087990%。

另有一個 finding 沒提的同根因問題：9/16 與 9/23 兩個分區各只有 1,330 列，全是 .TWSE，完全沒有上櫃股。9/23 的 log 記錄了 TPEx 抓取失敗（backend.log:37258）。fetch_date 把 TPEx 的失敗吞掉，只回傳上市資料（snapshot_provider.py:95-118）；refresh_dates 照樣寫入並算成功（daily_refresh.py:175），之後因為分區已存在就永遠跳過。結果是這兩天的上櫃資料永久缺漏。自選 9 檔都是上市股，不直接受影響；但全市場選股與策略會受影響。這件事可另立一條 finding。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:180：日線排程是 CronTrigger(hour=16, minute=30)，misfire_grace_time=3600（第 188 行）；21:40 那班在第 198 行，同樣呼叫 run_update，沒有傳 force（第 149-153 行）
      - backend/app/taiwan/daily_update.py:39-40：DEFAULT_POST_CLOSE_HOUR=16、MINUTE=0；第 115 行判斷過 16:00 後，目標日就是今天；第 282 行只補 d_max 之後的日期
      - backend/app/taiwan/daily_refresh.py:150、167-170：已有分區的日期直接 dates_skipped；bootstrap.py:397、448 也沒有 force
      - 官方 raw/twse_mi_index_20260923.json 的「每日收盤行情」表 notes：『本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。』
      - https://www.tejwin.com/insight/鉅額交易是什麼/（2024-07-19）：配對交易『08:00–08:30 及 09:00–17:00』，逐筆交易『09:00–17:00』；股票組合門檻『5 種股票且金額合計 1,500 萬元以上』
      - https://tw.stock.yahoo.com/news/9-18證交所鉅額交易日成交金額前20名排行-004300303.html：2330 配對交易 1,602,000／871,280／423,720／200,000 股，2454 為 383,000 股、4710 元，都能對上 store 在 9/18 缺少的量（2330 缺 3,702,177 股，2454 缺 434,469 股、隱含價 4710.0）
      - 本地（uv run python verify_f4_def/cmp.py）：9/16、9/17、9/18、9/22、9/23 的 2330、2454 等都偏低（2330 9/18 -9.05%，6196 9/17 -16.95%）；9/10～9/15 與 9/21 共 10 檔全部 OK；收盤價零差異
      - 本地（verify_f4_def/all923.py）：9/23 上市 1,330 檔 lower=23、higher=0、close mismatch=0，sum dv=12,235,374 股、sum da=3,926,807,712 元，whole-lot 22 檔，重現了 finding 的數字
      - data/backend.log.1:48885-48889：9/18 的排程抓取在 17:03:32～17:03:43 執行，已過 17:00，該分區的 2330 仍少 9.05%。延到 17:30 是否足夠沒有證據
      - 本地（verify_f4_def/enr.py）：kline_daily_enriched 2330 9/23 的 volume=21,978,873、turnover_rate=0.08475458642%；乘上官方量的比例後為 0.087990%，與 finding 一致
      - 新面向：9/16、9/23 分區各 1,330 列，全是 .TWSE、沒有 .TPEX；backend.log:37258『TPEx snapshot fetch failed for 2026-09-23: peer closed connection…』；snapshot_provider.py:95-118 吞掉 TPEx 失敗並回傳只有上市股的資料，之後同樣因為分區已存在而永不重抓
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：原敘述成立，數字全部重現。

**重現結果**
- 當日抓取的上市 MI_INDEX 分區（9/16、9/17、9/18、9/22、9/23）成交量與金額都低於官方終值，只少不多；開高低收不受影響。
- 9/23 全市場有 23 檔偏低，合計少 12,235,374 股、3,926,807,712 元。
- 自選的 2454 連 5 天偏低 0.08%～4.19%；6196 在 9/17 少 16.95%。

**根因**
- daily_update 在 d_max 已等於目標日時直接跳過（daily_update.py:136-137、282-287）。
- refresh_dates 也會跳過已有分區的日期（daily_refresh.py:167-170）。
- 因此 21:40 那班與之後的自動流程都不會重抓，只有手動 `--force` 會。

**補正**
1. 9/18 是在 17:03 抓的（已過 17:00 的鉅額收盤），仍然偏低：2330 -9.05%、2454 -4.19%。所以「把排程延到 17:30」是否足夠沒有證據。建議改為在 21:40 或隔日對最近 1～2 個交易日強制重抓。
2. 同一個「已有分區就跳過」的機制，也讓 9/16 與 9/23 的分區永久缺少全部上櫃資料（0 列；9/23 是 16:30 抓上櫃失敗後只寫入上市資料）。這個問題嚴重度可能更高，應另立 finding。
    - 理由：我寫了自己的腳本，用本地 parquet 對照官方參考資料（MI_INDEX 9/23 全市場、10 檔的 STOCK_DAY 8/03～9/23）。finding 裡的每個數字都逐一重現，程式碼的跳過機制與排程日誌也都對得上，所以判定 confirmed。

**數字並列（finding → 我的結果）**
- 9/23 上市比對：1,330 檔，偏低 23 檔、偏高 0 檔 → 23／0。其中整張數差額 22 檔 → 22。
- 9/23 總差額：成交量少 12,235,374 股、金額少 3,926,807,712 元 → 完全相同。
- 個股：6464 為 2,300 股對官方 302,300 股（-99.24%）；1109 為 -89.21%；2376 為 -33.08%；2330 為 -3.677%。都與 finding 相同。
- 自選股：2454 在 9/16、9/17、9/18、9/22、9/23 五天都偏低，依序為 -3.75%、-0.29%、-4.19%、-0.56%、-0.078%，與「0.08%～4.19%」一致；6196 9/17 為 -16.95%；3034 9/17 為 -1.46%；2379 9/18 為 -1.25%；3545 9/18 為 -0.63%。
- 開高低收：9/23 全市場 0 筆不符；10 檔的 38 天也都是 0 筆不符。
- 隔日才抓的分區：8/03～9/15（9/16 11:21 回補）與 9/21（9/22 10:55 抓），10 檔的量與金額都和官方完全相等。
- 換手率：enriched 裡 2330 9/23 的 turnover_rate 是 0.084755。用 repo 的 share_capital（25,932,370,067 股）重算，store 的量得 0.0847546%，官方的量得 0.0879899%。

**機制**
- daily_refresh.py:150,167-170：只要該日已有分區就直接跳過。
- daily_update.py:282-287 與 136-137：d_max 已經等於目標日時，根本不會呼叫 refresh。
- 9/23 21:40 那班的日誌（backend.log:45470-45478）只有 openapi 的請求，沒有 MI_INDEX 請求，確認沒有重抓。

**線上交叉驗證**
- Yahoo 奇摩股市的 9/18 鉅額交易前 20 名：2330 有 4 筆，合計 3,097,000 股（store 少 3,702,177 股）；2454 有 1 筆 383,000 股（store 少 434,469 股）。量級吻合，而且 store 短少的量不小於前 20 名的合計，支持「差額來自鉅額交易」。
- 這一點是推論，理由有三：差額多為整張數、只少不多、開高低收不受影響，都符合鉅額交易不影響開高低收的特性。

**需要補正的地方（不影響判定）**
1. 9/18 是在 17:03:32 抓的，已經過了 17:00 的鉅額收盤，數字仍然偏低（2330 -9.05%、2454 -4.19%）。所以 fix_suggestion 裡「延到 17:30 之後」是否足夠，沒有證據。較可靠的做法是在 21:40 或隔日強制重抓。
2. finding 漏列了幾個更大或額外的偏差：2330 9/18 -9.05%（樣本中最大）、2330 9/22 -5.08%、3034 9/18 -0.62%、6196 9/18 -0.14%。
3. 「永遠不會重抓」只適用於自動流程。手動 CLI 有 --force 可以重抓（daily_update.py:445）。

**新發現（同一根因，原 finding 沒提）**
9/16 與 9/23 的分區完全沒有上櫃資料（0 列，其他天約 1,006～1,009 列）。
- 9/23 的原因：16:30:05 抓上櫃資料失敗（backend.log:37258，incomplete chunked read），而 fetch_date 只要上市有資料就照樣回傳（snapshot_provider.py:117-124），於是只含上市的分區被寫入。
- 因為同一個「已有分區就跳過」的邏輯，21:40 那班沒有補抓。一次暫時性的連線失敗，就成了整個上櫃市場永久缺一天。
- 9/16 的日誌已經輪替掉，原因查不到。
- 使用者的自選股全是上市，不受直接影響；但上櫃的掃描與策略會缺這兩天。這一點建議另立 finding。
    - 證據：
      - 腳本 AUDIT_SCRATCH/work/verify-f4vol/verify.py；指令 `cd backend && uv run python verify.py`；輸出存在同目錄的 out.txt 與 partB.csv
      - Part A（9/23 store 與 MI_INDEX 全市場比對）：joined 1330；vol lower 23、higher 0；amount lower 23、higher 0；sum dvol -12,235,374；sum damt -3,926,807,712；whole-lot 22；OHLC mismatches 0
      - Part A 個股：6464 store 2300 vs 官方 302300（-99.239%）；1109 -89.213%；2376 -33.084%；2330 21,978,873 vs 22,817,873（-3.677%）；2454 -5000（-0.078%）
      - Part B（10 檔 38 天）：8/03～9/15 與 9/21 各 10 檔 vol_eq=10、amt_ne=0；9/16 偏低 2 檔、9/17 4 檔、9/18 6 檔、9/22 2 檔、9/23 2 檔；ohlc_bad 全為 0
      - Part B 補充：2330 9/18 為 37,190,511 vs 40,892,688（-9.05%），9/22 為 -5.08%；3034 9/18 -0.617%；6196 9/18 -0.143%（finding 未列）
      - 分區修改時間：9/16 16:30:22、9/17 16:19:15、9/18 17:03:37、9/22 16:30:03、9/23 16:30:05（當日抓取）；9/21 為 9/22 10:55:40；8/03～9/15 為 9/16 11:21
      - backend/app/taiwan/daily_refresh.py:150,167-170：available 集合含該日就 dates_skipped += 1 並 continue
      - backend/app/taiwan/daily_update.py:136-137,282-287：earliest_available >= target_latest 回傳 None，並註記 'Daily store already current'；:39 DEFAULT_POST_CLOSE_HOUR = 16；:445 CLI 有 --force
      - backend/app/jobs/daily_pipeline.py:180 CronTrigger hour=16 minute=30；:195 註解「已落盤的日期會被 refresh 略過, 不重複下載」；:198 21:40
      - data/backend.log:45470-45478：9/23 21:40 那班只有 openapi 請求（t187ap03_L、mopsfin），沒有 MI_INDEX 請求；backend.log.1:48885-48890：9/18 在 17:03:32 抓 MI_INDEX（scheduler 延遲執行）
      - enriched 分區 data\kline_daily_enriched\date=2026-09-23：2330 turnover_rate=0.084755；重算 21,978,873/25,932,370,067×100=0.0847546、22,817,873/25,932,370,067×100=0.0879899（share_capital.parquet 的 2330 issued_shares=25932370067）
      - 官方參考 raw\twse_mi_index_20260923.json 附註：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm（WebSearch 摘要）：鉅額逐筆交易 9:00～17:00，配對交易 8:00～8:30、9:00～17:00
      - https://tw.stock.yahoo.com/news/9-18%E8%AD%89%E4%BA%A4%E6%89%80%E9%89%85%E9%A1%8D%E4%BA%A4%E6%98%93%E6%97%A5%E6%88%90%E4%BA%A4%E9%87%91%E9%A1%8D%E5%89%8D20%E5%90%8D%E6%8E%92%E8%A1%8C-004300303.html：9/18 鉅額前 20 名，2330 有 1,602,000、871,280、423,720、200,000 股（合計 3,097,000）；2454 有 383,000 股 @4710。量級與 store 短少的 3,702,177 股、434,469 股吻合
      - https://tw.stock.yahoo.com/news/9-17%E8%AD%89%E4%BA%A4%E6%89%80%E9%89%85%E9%A1%8D%E4%BA%A4%E6%98%93%E6%97%A5%E6%88%90%E4%BA%A4%E9%87%91%E9%A1%8D%E5%89%8D20%E5%90%8D%E6%8E%92%E8%A1%8C-004300428.html：9/17 抓到的列中，2330 鉅額合計 402,205 股（store 短少 597,000 股）
      - 新面向：9/16 與 9/23 分區只有上市 1330 列、上櫃 0 列（9/15 為 1006 列、9/21 與 9/22 為 1009 列）；backend.log:37258「TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body」；snapshot_provider.py:117-124 上櫃失敗時仍回傳只含上市的資料並寫入
      - repo 工作樹在稽核前後的 git status 相同（只有原本就有的 3 個 M），我的輸出只寫在 scratchpad 的 verify-f4vol

### [P3 → P2] F9 創新板股票 7812 稜研科技不在證券主檔，沒有任何日K（部分成立）

- **主張**：MI_INDEX 9/23 列出 7812 稜研科技*-創（成交 225,228 股、收 70.9），但 security_master.parquet 沒有這檔，快照的 allowlist 會把它濾掉；最近 80 個分區都沒有它
- **正確的定義或數值**：創新板普通股納入主檔，或明確標示不支援
- **app 實際行為**：不聲不響地缺漏
- **對使用者的影響**：不在自選中；影響全市場統計與搜尋
- **修法建議**：由證券主檔（universe）領域確認創新板的分類規則，把它納入
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:81-93,179-180（allowlist 過濾）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - c4：MI_INDEX 有而 store 沒有的 50 個代號中，只有 7812 是普通股，其他是 REITs、ETN、特別股
  - security_master.parquet 查無 7812
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：證券主檔 security_master.parquet 是 2026-09-17 17:44 一次建立的快照。之後只要快取存在，就沒有任何路徑會更新它（service.py:134-144 只在快取不存在時抓官方資料；bootstrap 匯入是唯一的另一條重建路徑）。因此 9/17 之後上市的股票會被快照的 allowlist 安靜地濾掉，沒有日K，也搜尋不到。

目前確認受影響的有兩檔：
- 上市：7812 稜研科技*-創，2026-09-22 在創新板上市。MI_INDEX 9/23 成交 225,228 股、收 70.90。
- 上櫃：7856 漢測，2026-09-22 轉上櫃。9/23 成交 631,287 股、金額 29.58 億元、收 4,730。

這不是創新板分類的問題：MI_INDEX 9/23 的 31 檔創新板股票中，有 30 檔已在主檔。「最近 80 個分區都沒有」雖然屬實，但 7812 在 9/22 才上市，只有其中兩個分區可能出現它。修正方向是讓主檔定期或每日重新抓官方名錄，或在快照看到主檔沒有、但確實有成交的普通股代號時觸發更新，而不是調整創新板的分類規則。
    - 理由：finding 裡的數字都重現了，但它把根因歸給「創新板分類」，這是錯的。

一、重現的數字（我算出的值與 finding 的值並列）
- MI_INDEX 9/23 的 7812 稜研科技*-創：成交 225,228 股、收盤 70.90。finding 寫 225,228 股、收 70.9，一致。
- security_master.parquet 共 2,377 列，沒有 7812，與 finding 一致。snapshot_provider.py 的 allowlist 過濾會把它濾掉，也一致。
- MI_INDEX 9/23 有、但 store 9/23 的 TWSE 列沒有的代號共 50 個：受益證券（REITs，01001T～01010T）6 個、ETN（020000～020039、02001L）15 個、特別股 28 個，普通股只有 7812。與 finding 的「50 個中只有 7812 是普通股」一致。
- 最近 80 個分區（date=2026-06-02 → 2026-09-23）都沒有 7812.*，數字一致。但這個說法會誤導：7812 在 2026-09-22 才上市，80 個分區裡只有 9/22、9/23 兩個有可能出現它。

二、錯誤的部分：根因不是創新板
- MI_INDEX 9/23 帶「-創／KY創」的創新板股票共 31 檔。其中 30 檔都在主檔裡，is_supported=True，並且在 TWSE allowlist 內，例如 2237 華德動能-創（上市日 20260810）、7835 永悅健康-創（20260724）。缺漏的只有 7812。
- 主檔程式並沒有排除創新板。OpenAPI 路徑直接收錄 t187ap03_L 的所有上市公司；ISIN 路徑也明確把「創新板」歸為 stock。
- 真正的原因是證券主檔從未更新：
  - 主檔全部 2,377 列的 updated_at 都落在 2026-09-17 17:43:56～17:44:22 之間，parquet 的修改時間也是 9/17 17:44。
  - 官方 t187ap03_L 記載 7812 的上市日期是 2026-09-22，而且它是官方上市公司名錄裡唯一上市日期 ≥ 9/17 的公司。
  - 程式裡只有兩條路徑會重建主檔：一是 ensure_loaded() 在快取不存在時才抓官方資料並存檔；二是 bootstrap 匯入。沒有任何排程或 API 會定期更新它。
- 同一個原因也讓上櫃新股 7856 漢測漏掉。它在 2026-09-22 轉上櫃，9/23 成交 631,287 股、金額 2,957,653,110 元、收盤 4,730，主檔和 9/22、9/23 分區都沒有它。
- 比對官方名錄：t187ap03_L 有 1,095 家，主檔只缺 7812；mopsfin_t187ap03_O 有 893 家，主檔只缺 7856。兩者都是 9/22 新上市或上櫃，所以問題與創新板無關，而是在 9/17 快取建立之後上市的新股都會被永久漏掉。

三、嚴重度
finding 描述的是「單一創新板股票缺漏」，照這個描述定 P3。實際問題更普遍：只要主檔快取存在就永遠不會更新，兩個交易所之後的每一檔新上市股都會安靜地缺席，沒有日K、搜尋不到、全市場統計也不計入。依 CONTRIBUTING.md:296，這屬於 P2「明確功能錯誤」。使用者的 9 檔自選不受影響，所以不到 P1。

四、修正建議也要跟著改
finding 建議「確認創新板分類規則」，這修不到問題。應該讓證券主檔定期或每日重新抓官方名錄；或者在快照看到主檔沒有、但確實有成交的 4 碼普通股代號時，觸發主檔更新。

五、題外觀察，不屬於本 finding
store 的 date=2026-09-23 分區只有 1,330 個 symbol，全是 .TWSE；9/22 分區則有 2,340 個。9/23 可能缺上櫃資料，原因我沒有查。

六、工作樹
驗證前後 git status 相同，只有原本就有的 3 個改動；security_master.parquet 的修改時間仍是 9/17 17:44。
    - 證據：
      - 官方 MI_INDEX 原始檔 AUDIT_SCRATCH/reference/raw/twse_mi_index_20260923.json 的 7812 列：["7812","稜研科技*-創","225,228","227","16,328,422","73.00","74.40","70.60","70.90",...]；腳本：scratchpad\audit\work\verify-f9-7812\verify.py，輸出在 out.txt
      - security_master.parquet 共 2,377 列，updated_at 介於 2026-09-17T17:43:56.878998+08:00 和 2026-09-17T17:44:22.169784+08:00 之間；7812 的筆數為 0；來源分布 TWSE_OPENAPI 1365、TPEX_OPENAPI 892、TPEX_ISIN 120（verify.py 輸出）
      - MI_INDEX 9/23 有而 store 9/23 TWSE 列沒有的代號共 50 個：受益證券 01001T～01010T 6 個、ETN 020000～020039 與 02001L 共 15 個、特別股（1101B、2881A、2887Z1 等）28 個，另加 7812（verify.py 輸出）
      - 最近 80 個分區（date=2026-06-02 → 2026-09-23）都沒有 7812.*；date=2026-09-22 有 2,340 個 symbol、date=2026-09-23 有 1,330 個，兩者都沒有 7812 和 7856
      - MI_INDEX 9/23 的創新板（-創／KY創）股票共 31 檔，其中 30 檔在主檔且在 allowlist 內，例如 2237 華德動能-創 listing_date=20260810、7835 永悅健康-創 20260724、7803 雲象科技-創 20260520；只有 7812 缺（verify.py 輸出）
      - 官方 t187ap03_L（reference\csv\twse_t187ap03_L_share_capital.csv）：7812 稜研科技*-創 listing_date=2026-09-22、issued_common_shares=64398521、par_value_raw=無面額；它是唯一上市日期 ≥ 2026-09-17 的公司
      - 官方名錄對照主檔（verify2.py 輸出在 out2.txt）：TWSE 公司 1,095 家，主檔缺 [('7812','稜研科技*-創','2026-09-22')]；TPEX 公司 893 家，主檔缺 [('7856','漢測','2026-09-22')]
      - 官方 TPEx dailyQuotes 9/23（reference\csv\tpex_daily_quotes_20260923.csv:11581）：7856 漢測 收盤 4730.0、成交 631287 股、金額 2957653110 元
      - backend/app/taiwan/universe/service.py:134-144：ensure_loaded 在 load_cache() 成功時直接 return，只有快取不存在時才呼叫 load_from_adapters() 和 save_cache()；在 backend\app 內 grep load_from_adapters|save_cache，除了 service.py 自己之外沒有其他呼叫者
      - backend/app/taiwan/universe/adapters.py:397-421：_official_company_directory 把 t187ap03_L／mopsfin_t187ap03_O 的每一家公司都當成 stock、is_supported=True，不看板別；adapters.py:253 的 ISIN 路徑明確把「創新板」歸為 stock
      - backend/app/taiwan/providers/snapshot_provider.py:81-93、178-180：allowlist 只收錄主檔中 is_supported 的 stock／etf，不在清單上的代號直接 continue 跳過
      - 線上來源 Yahoo 奇摩股市 https://tw.stock.yahoo.com/news/%E7%A8%9C%E7%A0%94%E7%A7%91%E6%8A%80-%E5%89%B5%E6%99%AE%E9%80%9A%E8%82%A1%E6%9A%A8%E7%8F%BE%E5%A2%9E%E8%82%A1%E6%AC%BE%E7%B9%B3%E7%B4%8D%E6%86%91%E8%AD%899-22%E8%B5%B7%E5%89%B5%E6%96%B0%E6%9D%BF%E4%B8%8A%E5%B8%82%E8%B2%B7%E8%B3%A3-002100409.html：搜尋摘要的文字是「稜研科技…普通股股票暨現金增資股款繳納憑證訂於民國115年9月22日於創新板上市開始買賣」，股數為普通股 60,418,521 股加繳納憑證 3,980,000 股，合計 64,398,521 股，與 t187ap03_L 一致
      - 線上來源 工商時報 https://www.ctee.com.tw/news/20260922700583-430201 的標題「《櫃買市場》漢測 9月22日2250元上櫃」；聯合新聞網 https://udn.com/news/story/7252/9765447 的標題「興櫃股王漢測9月22日轉上櫃」
      - 派工前後 git status 相同（只有 daily_update.py、pnpm-lock.yaml、vite.config.d.ts 這 3 個原本就有的改動）；security_master.parquet 的修改時間仍是 Sep 17 17:44

### [P3 → P3] F6 data/kline_daily 殘留日期錯置的舊列（B3 殘留），仍被資料狀態統計與 data_integrity 讀取（部分成立）

- **主張**：B3 修正前寫入的 kline_daily 分區沒有清除：date=2026-09-17 的內容是 9/16 的行情（2454 close 4530＝官方 9/16 收盤），date=09-22 是 9/21 的行情（2454 5010），9/16～9/18 的 quote_ts 是微秒（B2 殘留）。repository 的 latest_daily_date 等 kline_daily 統計，以及 F5 的完整性掃描都會讀到它們
- **正確的定義或數值**：修正 B3 之後，清理或隔離已寫入的錯置分區
- **app 實際行為**：錯置列仍在，且被統計與完整性掃描使用
- **對使用者的影響**：資料頁的「日K最新日期」等統計與 F5 的誤報，都以這些錯置資料為準；台股 K 線顯示不讀這張表，所以價格畫面不受影響
- **修法建議**：把 data/kline_daily 的 9/16～9/22 分區移到隔離資料夾，並在台股部署停用 kline_daily 相關統計
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:1927（SELECT max(date) FROM kline_daily）
  - backend/app/api/data.py:359
- **線上證據**：
  - （無）
- **本地計算證據**：
  - c5_kline_daily.py：date=2026-09-17 的 2454 為 open 4435／close 4530，對應官方 STOCK_DAY 9/16 的 4435／4530；date=2026-09-22 的 2454 為 close 5010，對應官方 9/21 的 5010；date=2026-09-16 的 quote_ts 為 1789450200000000（微秒，換算為 9/15 13:30）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：B3（以及 B2）修正前寫入的 data/kline_daily 分區沒有清除。4 個分區共 33 列，全部是「前一個交易日」的官方行情：date=9/16 是 9/15 的行情，9/17 是 9/16（2454 收盤 4530），9/18 是 9/17，9/22 是 9/21（2454 收盤 5010）。9/16～9/18 的 quote_ts 是微秒，換算後是前一交易日 13:30；9/22 的 quote_ts 是 null。這些分區讓 repository 的 latest/earliest_daily_date 以及 /api/data/status 的日K統計變成 9/16～9/22、共 4 天。前端沒有頁面讀 /api/data/status，台股的資料狀態與 K 線都讀 TaiwanDailyStore，所以這些錯誤統計不會出現在畫面上。實際可見的後果只有一個：kline_etf_daily 與 kline_index_daily 都是空的，kline_daily 因為這些殘留分區被 data_integrity 當成「近期有活動」的表族，於是 9/24～9/29 會回報 kline_daily 缺 9/23 等日期，並觸發自動修復任務。backend.log 已有「2026-09-23 的数据为缺失」的紀錄。建議把這 4 個分區移到隔離資料夾。
    - 理由：數值部分完全重現，而且比 finding 寫的更全面：data/kline_daily 的 4 個分區共 33 列，每一列的 OHLC 與成交股數都和「前一個交易日」的官方 STOCK_DAY 逐欄相同（33/33），與分區同日的官方資料則沒有一列相同（0/33）。finding 只點名 9/17 與 9/22 兩個分區，其實 9/16 分區是 9/15 的行情，9/18 分區是 9/17 的行情，4 個分區全部錯置。9/16～9/18 的 quote_ts 用微秒解讀，得到的都是前一個交易日 13:30；9/22 分區的 quote_ts 全部是 null。4 個檔案的修改時間都早於 B2/B3 的修正 commit f43c7bd（2026-09-22 19:36:04），和「修正前寫入的殘留」相符。

finding 引用的程式碼行號正確，完整性掃描也確實讀到這些分區。我把分區複製到 scratchpad 後呼叫 scan_recent_integrity 重跑：今天設為 9/24 時，回報 [('2026-09-23','kline_daily','missing')]，而且落在自動修復窗口內；9/25～9/29 會持續回報；拿掉殘留分區之後，任何一天都沒有 issue。kline_etf_daily 與 kline_index_daily 都是 0 個分區，所以 kline_daily 是唯一被掃描的表族，而它被視為「近期有活動」的唯一原因就是這些殘留分區。backend.log 裡「2026-09-23 的数据为缺失 (共 1 个坏分区)」與自動修復任務的紀錄和這個結果一致。要注意的是，觸發掃描的原因是「殘留分區存在」，不是「分區內容錯置」本身。

需要修正的是 user_impact。finding 說「資料頁的日K最新日期等統計以錯置資料為準」，但前端沒有任何頁面讀 /api/data/status：useDataStatus 只有定義，沒有呼叫者。台股的資料狀態、歷史資料卡片與個股日K都讀 TaiwanDailyStore，不讀 kline_daily。所以後端統計值雖然錯（earliest 9/16、latest 9/22、4 天），使用者在畫面上看不到。其他讀取點的影響也很小：kline.py 分時端點的 fallback 判斷，不論有沒有殘留分區，只要今天大於 9/22 結果都一樣；backtest 的 stream 端點用 earliest_daily_date，但前端沒有呼叫這些端點。嚴重度維持 P3。完整性掃描誤報、進而觸發修復任務的後果屬於 F5 的範圍。
    - 證據：
      - 本地重算（AUDIT_SCRATCH/work/verify-f6/compare.py，指令為 uv run python compare.py）：kline_daily 共 33 列，每列的 OHLC 與 volume 都和官方 twse_stock_day_202608_202609.csv 前一個交易日的資料完全相同（33/33），與同日官方資料相符的是 0/33
      - 2454 對照（官方原始檔 raw\twse_stock_day_2454_202609.json）：分區 date=2026-09-17 是 4435/4625/4435/4530、量 6,640,865，對應官方 115/09/16 的 '4,435.00','4,625.00','4,435.00','4,530.00'、'6,640,865'（finding 寫 4435／4530，一致）；官方 9/17 實際是 4565/4700/4500/4500
      - 2454 對照：分區 date=2026-09-22 是 4800/5035/4780/5010、量 11,237,165，對應官方 115/09/21 的 '4,800.00','5,035.00','4,780.00','5,010.00'、'11,237,165'（finding 寫 5010，一致）；官方 9/22 實際收盤是 5,180
      - finding 沒提到的部分：分區 date=2026-09-16 共 6 列，全部是 9/15 的行情，例如 0050 為 106.75/106.95/106.15/106.25、量 63,373,313，等於官方 9/15；分區 date=2026-09-18 的 2454 是 4565/4700/4500/4500，等於官方 9/17
      - quote_ts：9/16 為 1789450200000000，以微秒換算是 2026-09-15 13:30+08（finding 寫的數值與換算都一致）；9/17 為 1789536600000000，換算是 9/16 13:30；9/18 為 1789623000000000，換算是 9/17 13:30；9/22 全部是 null。以毫秒解讀時 fromtimestamp 丟出 OSError，所以 _is_snapshot 一律回傳 False（見 scan.py 的輸出）
      - 檔案修改時間（ls --time-style=full-iso）：9/16 17:03:10、9/17 23:43:44、9/18 14:59:47、9/22 19:16:35，全部早於 B2/B3 修正 commit f43c7bd 的 2026-09-22 19:36:04 +0800
      - DuckDB 唯讀查詢 kline_daily view（stats.py）：min(date)=2026-09-16、max(date)=2026-09-22、count=33、distinct date=4
      - backend/app/repository.py:1927：latest_daily_date 執行 SELECT max(date) FROM kline_daily；repository.py:1899：earliest_daily_date 執行 SELECT min(date)
      - backend/app/api/data.py:137-155：_safe_aggregate_daily 從 kline_daily 的分區目錄名取 earliest/latest/trading_days；data.py:359：adj_factor 統計以 kline_daily 的 min/max 當日期範圍
      - backend/app/services/data_integrity.py:44：_DAILY_TABLES 包含 kline_daily。重跑 scan_recent_integrity（scan.py，資料用複本）：有殘留時，today=9/24 得到 [('2026-09-23','kline_daily','missing')]、auto_window=True；9/29 得到 9/23～9/28 共 4 筆 missing；9/30 沒有 issue。沒有殘留時，4 個日期都沒有 issue
      - data 目錄實查：kline_etf_daily 與 kline_index_daily 都是 0 個分區，kline_daily 是唯一被掃描的表族。data/backend.log:46496 記錄「boot integrity check: 2026-09-23 的数据为缺失 (共 1 个坏分区)」，46498 接著啟動自動修復任務
      - UI 影響需要修正：frontend/src/lib/useSharedQueries.ts:72 的 useDataStatus 在 frontend/src 沒有任何呼叫者，/api/data/status 只在 api.ts:2739 被引用。台股資料狀態頁使用的端點與其資料來源：taiwan.py:70 → daily_update.py:192-195 get_freshness，bootstrap.py:68-75 get_history_status，兩者都讀 TaiwanDailyStore；個股日K走 detail_service.py:141,269，同樣讀 TaiwanDailyStore
      - 其他讀取點：kline.py:707/713/896/902 的分時端點 fallback，今天大於 9/22 時，有沒有殘留分區結果都相同（None 也會觸發 fallback）；backtest.py:506/807/1029 的 strategy/optimize/walkforward stream 端點使用 earliest_daily_date，但 frontend/src 沒有呼叫這些路由

### [P3 → P3] F7 enriched 歷史分區欄位不一致，且 enriched_refresh 的 docstring 與實作矛盾（部分成立）

- **主張**：refresh_recent 只重建最近 5 個分區。661 個 enriched 分區中有 651 個只有 65 欄，缺 signal_limit_*、turnover_rate、consecutive_limit_*；4 個 69 欄；只有 6 個是完整的 72 欄。模組 docstring 寫「Limit-board signals stay absent」，但同檔已經實作 _add_limit_signals
- **正確的定義或數值**：新增欄位後做一次全量重建（enriched_refresh --full），讓歷史分區的 schema 一致；docstring 與行為一致
- **app 實際行為**：歷史分區缺欄；docstring 過時
- **對使用者的影響**：任何以歷史日期查詢漲跌停、換手率或連板數的功能（回測、歷史選股）都只會拿到 null
- **修法建議**：執行一次 python -m app.taiwan.enriched_refresh --full（需使用者同意寫入資料），並更新 docstring
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:9-10
  - backend/app/taiwan/enriched_refresh.py:191-198
- **線上證據**：
  - （無）
- **本地計算證據**：
  - c3：enriched 欄數分布 {65: 651, 69: 4, 72: 6}；9/10 之前的分區讀 signal_limit_up 會 ColumnNotFound（c7 第一次執行時的錯誤）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：成立的部分：refresh_recent 預設只重建最近 5 個分區（enriched_refresh.py:191-198），661 個 enriched 分區的欄數分布是 {65: 651（2024-01-02..2026-09-09）, 69: 4（9/10–9/15）, 72: 6（9/16–9/23）}，已重現。65 欄的分區缺 turnover_rate、四個 signal_limit_*、consecutive_limit_ups/downs；69 欄的分區缺 turnover_rate 和 consecutive_limit_*。模組 docstring 第 9-10 行寫「Limit-board signals stay absent」，但 fc7e40a 之後 7a02944 已經加入 _add_limit_signals，docstring 過時。

需要修正的影響描述：
1. 缺欄只會讓兩類讀取者拿到 null：
   - 透過 DuckDB view（union_by_name）讀歷史 signal_limit_* 與 consecutive_limit_* 的查詢。
   - 直接讀已存 consecutive_limit_ups 的回測因子 limit_up_count_20d/60d。null 會被當成 0，歷史值因此靜默歸零。
   repository 的 polars 讀取路徑會從 OHLC 重算漲跌停訊號，不受影響。
2. 歷史 turnover_rate 為 null 是刻意設計（share_capital.py:39-51 只保留當日股本），跟 schema 無關，--full 重建也不會補上。

修法要先修另一個 bug 才能重建：已存的 consecutive_limit_* 是 compute_limit_signals 用 A 股 0.01 元四捨五入算出的漲停價（price_limits.py:133-138），不是台股 tick 捨去。_add_limit_signals 只覆寫了 signal_limit_*，所以兩組欄位互相矛盾。以 9/23 上市官方收盤資料對帳，15 檔收在漲停，signal_limit_up 15 檔全中，consecutive_limit_ups>0 只有 2 檔。因此要先讓 consecutive_limit_* 改由台股漲停判定推導，再執行 --full（需使用者同意寫入資料），並更新 docstring；否則重建只會把少算的連板數回填到整段歷史。連板數少算建議另開一條 finding，初判 P2。
    - 理由：我自己重算後，finding 列出的數字全部重現。問題出在它對影響的描述和修法建議，有兩處不對，另外我找到一個原記錄沒提到的新面向。

一、重現結果（我算的數字對照 finding 的數字）
- 分區數：我算 661，finding 寫 661。
- 欄數分布：我算 {65: 651, 69: 4, 72: 6}，finding 寫 {65: 651, 69: 4, 72: 6}，完全一致。
- 65 欄的分區是 2024-01-02..2026-09-09，缺 7 欄：turnover_rate、signal_limit_up、signal_limit_down、signal_broken_limit_up、signal_limit_down_recovery、consecutive_limit_ups、consecutive_limit_downs。
- 69 欄的分區是 9/10、9/11、9/14、9/15，缺 turnover_rate 和 consecutive_limit_*。
- 72 欄的分區是 9/16..9/23。
- 直接用 pl.scan_parquet 讀 signal_limit_up 會出現 ColumnNotFoundError，這點也重現了。
- refresh_recent 只重建最近 5 個分區：程式碼（enriched_refresh.py:191-198）和檔案時間都吻合。9/17..9/23 這 5 個分區的 mtime 都是 2026-09-23 22:07:41。
- docstring 過時屬實：line 9-10 寫「Limit-board signals stay absent」，但 line 98 呼叫了 _add_limit_signals，實作在 157-189。git 紀錄顯示這段 docstring 是 fc7e40a 寫的，之後 7a02944 加上 _add_limit_signals 時沒有跟著改。

二、user_impact 與修法不對的地方
1. 換手率的歸因錯了。歷史日期的 turnover_rate 是 null，這是刻意設計，跟 schema 缺欄無關：
   - share_capital.py:39-51 會把「台股且日期不等於今天」的 float_shares 設成 null。
   - pipeline.py:97 的註解寫明「不可回推」。
   - enriched_refresh 呼叫 compute_enriched 時沒有傳 historical_shares。
   - 實測：已經有該欄的 72 欄分區中，9/16..9/22 的 turnover_rate 非 null 數都是 0，只有 9/23 有 1087 筆。
   所以就算跑 --full，歷史換手率仍然全是 null。
2. 「回測、歷史選股都只拿到 null」說得太廣。
   - repository 的 polars 讀取路徑用 scan_enriched_parquet，它的 schema 固定只有 15 欄，本來就不會讀到 signal_limit_*。這條路徑會從 OHLC 重新計算訊號（repository.py:566-577），所以歷史漲跌停訊號不受缺欄影響。
   - 真正吃到 null 的有兩處：
     - DuckDB view（union_by_name，repository.py:168）：9/10 以前共 1,402,478 列，這 7 欄的非 null 數全是 0。
     - 直接讀已存欄位的 consecutive_limit_ups：回測因子 limit_up_count_20d/60d 會用到（factor.py:437、matrix.py:4028）。null 經 np.isfinite 過濾後被當成 0，所以歷史值會靜默變成 0，而不是 null。

三、新面向（docs/code-review-2026-09-22.md 沒有記錄）
「做一次全量重建」會把錯的 consecutive_limit_* 回填到整段歷史。
- 成因：這兩欄由 compute_limit_signals 產生。它用 polars_limit_price 算漲停價（price_limits.py:133-138），這是 A 股規則：四捨五入到 0.01 元，沒有照台股升降單位（tick）無條件捨去。之後 _add_limit_signals 用台股規則覆寫了 signal_limit_*，卻沒有重算 consecutive_limit_*，兩組欄位因此互相矛盾。
- 官方資料對帳（2026-09-23 上市 MI_INDEX 參考檔）：以「前收 × 1.1、再依 tick 無條件捨去」計算，有 15 檔收在漲停價。已存的 signal_limit_up 15 檔全中，沒有多報；但 consecutive_limit_ups > 0 只有 2 檔（4770、6243，這兩檔前收乘 1.1 剛好整除）。
- 9/16..9/23 各日 signal_limit_up 與 consecutive_limit_ups>0 不一致的筆數依序是 20、22、54、59、25、13。

四、嚴重度
F7 本身（schema 不一致、docstring 過時）維持 P3。上面的新面向屬於另一條 finding：連板數有系統性少算，建議另開，初判 P2。
    - 證據：
      - 本地重算 schema_census.py（AUDIT_SCRATCH/work/verify-f7/schema_census.py），輸出：partition dirs: 661；column-count distribution: {65: 651, 69: 4, 72: 6}；width 65: 2024-01-02 .. 2026-09-09；width 69: 2026-09-10 .. 2026-09-15；width 72: 2026-09-16 .. 2026-09-23；width 65 missing: turnover_rate, signal_limit_up, consecutive_limit_ups, signal_limit_down, consecutive_limit_downs, signal_limit_down_recovery, signal_broken_limit_up
      - reader_behavior.py 輸出：直接 pl.scan_parquet 回報 ColumnNotFoundError unable to find column "signal_limit_up"；DuckDB union_by_name 下 9/10 前 1,402,478 列，7 欄非 null 數全為 0；scan_enriched_parquet 只有 15 欄、不含 signal_limit_up；polars compat scan 未指定 schema 時只有 65 欄
      - backend/app/taiwan/enriched_refresh.py:9-10 docstring『Limit-board signals stay absent』；同檔 :98 呼叫 _add_limit_signals，實作在 :157-189；:191-198 refresh_recent(days=5)
      - git log -S：docstring 在 fc7e40a（2026-09-16）寫入；_add_limit_signals 在 7a02944（2026-09-17）加入時沒有更新 docstring；as_instruments/turnover 在 ebcce1a（2026-09-22）加入
      - 分區 mtime：9/09 以前是 2026-09-16 22:51；9/10 是 09-17 16:06；9/15 是 09-22 15:40（在 turnover commit 17:30 之前，故 69 欄）；9/16 是 09-22 19:32（在其之後，故 72 欄）；9/17..9/23 都是 09-23 22:07:41（refresh_recent 的 5 個分區）
      - backend/app/share_capital.py:39-51 把台股且 date != today 的 float_shares 設成 null；backend/app/indicators/pipeline.py:97 註解『依赖当时的 float_shares, 不可回推』。實測 72 欄分區 9/16..9/22 的 turnover_rate 非 null 數都是 0，9/23 有 1087 筆：歷史換手率為 null 是刻意設計，--full 重建也不會補上
      - backend/app/parquet.py:20-36,52-55 scan_enriched_parquet 的 schema 固定 15 欄；backend/app/repository.py:566-577 讀 OHLC 後重算指標與訊號；backend/app/repository.py:168 DuckDB view 用 union_by_name=true
      - backend/app/backtest/factor.py:437 與 backend/app/backtest/matrix.py:4028-4031 limit_up_count_20d/60d 直接讀已存的 consecutive_limit_ups；null 經 np.isfinite 判定後當成 0
      - 新面向：backend/app/indicators/pipeline.py:781-785,823-858 用 polars_limit_price 產生 signal_limit_up 與 consecutive_limit_ups；backend/app/price_limits.py:133-138 以 0.01 元四捨五入，沒有台股 tick 捨去；enriched_refresh.py:182-189 只覆寫 signal_limit_*，沒有重算 consecutive_limit_*
      - official_limit_check.py 以官方參考檔 twse_mi_index_20260923_quotes.csv 對帳：上市 4 碼股 1085 檔，其中 15 檔收在『前收×1.1 依 tick 捨去』的漲停價（例：3653 前收 5830、收 6410；1235 前收 37.45、收 41.15）。已存 signal_limit_up 15/15 命中、0 檔多報；consecutive_limit_ups>0 只有 2/15（4770、6243）
      - consistency.py：9/16..9/23 各日 signal_limit_up 與 (consecutive_limit_ups>0) 不一致筆數依序為 20、22、54、59、25、13，全部是 signal=true 而連板數=0；9/18 抽樣 1560.TWSE 前收 815、收 896（815×1.1=896.5，依 1 元 tick 捨去為 896）
      - 線上來源 https://winvest.tw/Knowledge/Article/348：『向下(漲停)或向上(跌停)調整到最接近、但仍不超過限制的價格』；升降單位表 10元以下 0.01、10～50元 0.05、50～100元 0.1、100～500元 0.5、500～1000元 1、1000元以上 5；範例 73.20×1.1=80.52 調為 80.50
      - docs/code-review-2026-09-22.md 裡搜尋 tick、升降單位、polars_limit_price、limit_up_count，都沒有相關記錄（只有第 20 行 SSE tick 無關命中），可見連板數少算的問題尚未被記錄
      - 另見（範圍外，未深入）：9/16 與 9/23 分區各只有 1319、1323 列，其他交易日約 2300 列；9/23 分區全是 .TWSE，推測當時上櫃資料尚未入庫

### [P3 → P3] F8 快照解析器不比對回應本身的日期，一律以請求日期標記（部分成立）

- **主張**：fetch_twse、fetch_tpex 只檢查 stat 與表格標題，把每一列都標成 target_date，不讀回應 JSON 的 date 欄（例如 dailyQuotes 回傳 "date":"20260923"）。manifest 記載 TPEx 有些端點在日期格式不符時會默默回傳最新資料；若 TPEx 對未公布或休市日回傳最新資料，就會被寫成錯的日期。這與 N4（法人／融資）同類，但發生在日K快照
- **正確的定義或數值**：回應日期與請求日期不一致時丟棄該批資料
- **app 實際行為**：不檢查
- **對使用者的影響**：目前沒有觀察到錯日；休市日、颱風假或手動 target-date 時才可能觸發
- **修法建議**：解析時讀 payload 的 date 欄（TWSE 的 params.date 或 date、TPEx 的 date），不等於 target_date 就回傳空 frame
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:126-152
  - backend/app/taiwan/providers/snapshot_provider.py:212-234
- **線上證據**：
  - （無）
- **本地計算證據**：
  - manifest global_limitations：「TPEx 有些端點在日期格式不符時會默默回傳最新資料（repo index_daily.py:29 的註解有提到）」；reference\raw\tpex_daily_quotes_20260923.json 含 date 欄
  - 目前的歷史資料經 c9 驗證沒有錯日（9/22 上櫃 965 檔一致），所以這項目前只是風險
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：OfficialDailySnapshotAdapter 的 fetch_twse 與 fetch_tpex 都以請求日期（target_date）標記每一列（snapshot_provider.py:196、:272），不比對回應自帶的日期。可比對的欄位有：TWSE 的頂層 date 與 params.date；TPEx 的頂層 date="20260923"，以及表格 date="115/09/23"。另外，只有 TWSE 會檢查 stat，TPEx 完全不檢查 stat。用官方 9/23 的回應、改以 9/24 或 10/09 請求時，會原樣產出標成錯誤日期的資料（已重現）。

目前沒有任何錯日資料。民國日期格式對過去交易日有效：9/11、9/14 是在 9/15 已公布後才補抓的，數值與官方各自當日相符。manifest 所說的「默默回傳最新資料」是指月指數端點 inx，不是 dailyQuotes。

實際風險只存在於「TPEx 在平日休市日或尚未公布時回傳其他日資料」這種未經驗證的情況。由於預設交易日曆不含任何假日、排程週一到週五執行，2026-09-25、09-28、10-09、10-26 會實際送出這類請求；若 TPEx 當時回的不是空資料，就會寫出只有上櫃列、日期錯誤的分區，而且之後不會再修正。

建議解析時比對 payload 的 date，不符就回空 frame，並在 TPEx 補上 stat 檢查。此項與 N4 同類、位置不同，屬防禦性加強（P3）。
    - 理由：「不比對回應日期」這個機制已完整重現。我用 httpx.MockTransport 把官方 9/23 的原始回應（TWSE 頂層 date=20260923、params.date=20260923；TPEx 頂層 date=20260923、表格 date=115/09/23）餵給真正的 OfficialDailySnapshotAdapter.fetch_date，並把 target_date 分別設成 2026-09-24 與 2026-10-09。輸出列全被標成請求日期，數值與 9/23 完全相同，例如 2454 收盤 5185、6488 量 28,714,838。這證明解析器確實不看回應日期。

finding 其他幾點有誤或沒有根據：
(1) 「只檢查 stat 與表格標題」不準確：fetch_twse 有檢查 stat=="OK"，fetch_tpex 完全不檢查 stat，只看標題與欄位。
(2) manifest 所謂「TPEx 會默默回傳最新資料」，出處是 index_daily.py:29 的註解，講的是月指數端點 inx 的日期不含日時，會回當月資料，不是 dailyQuotes。
(3) 用本地資料實測，dailyQuotes 的民國日期格式對過去交易日有效。2026-09-11、09-14 兩個分區是 9/16 11:21 由快照補抓寫入的，當時已公布的最新日是 9/15。兩個分區裡 6488、8069 的量與收盤都與官方各自當日相符，也不等於 9/15 的值。全市場上櫃同收盤且同量的只有 0～2 檔／約 995 檔，沒有出現「回傳最新資料」。
(4) 已記錄的 N4 實測過：TPEx 對未來日期回 stat=ok、日期與請求相同、資料為空。
(5) 本地 661 個分區沒有任何錯日跡象：沒有週末分區；2024-08-01～2026-09-17 間 519 個分區與加權指數日曆完全一致；相鄰日同 OHLCV 比例最高 0.30%。

因此，「會把錯的日期寫進資料」目前只是條件式風險，觸發條件是 TPEx 在休市日回傳最新資料，而這一點無法在不連 TPEx 的前提下驗證。不過這個條件近期就會被實際測到：排程器週一到週五 16:30 與 21:40 會執行，預設的 TaiwanTradingCalendar 沒有任何假日，所以平日休市日會被請求。2026-09-25 中秋、09-28 教師節、10-09、10-26 都是平日休市。TWSE 在休市日會回非 OK 而被略過，結果完全取決於 TPEx 的行為；萬一誤標，會產生只有上櫃列的分區，之後因為日期已在 available_dates 而永遠不會再修正。

這與 N4 同類，但位置在 snapshot_provider.py，原記錄沒有涵蓋，嚴重度維持 P3（防禦性加強）。

另案（不屬於 F8，建議另行追蹤）：
- 9/16 與 9/23 分區的上櫃列數是 0。9/23 是 16:30:05 TPEx 請求發生 incomplete chunked read；因為分區已存在，21:40 那次補跑也跳過了，上櫃資料永久缺漏。
- 16:30 抓到的上市量與隔日官方值不一致，例如 2454 在 9/18 為 9,930,705 對 10,365,174，而且之後不再更新。
    - 證據：
      - 程式碼：backend/app/taiwan/providers/snapshot_provider.py:196 與 :272 以 "date": target_date 標記每列；:132-135 只有 fetch_twse 檢查 stat；:219-226 的 fetch_tpex 沒有檢查 stat，也沒有讀 payload 的 date。
      - 官方回應格式：reference\raw\tpex_daily_quotes_20260923.json 頂層 date="20260923"、tables[0].date="115/09/23"、stat="ok"；reference\raw\twse_mi_index_20260923.json 頂層 date="20260923"、params.date="20260923"、stat="OK"（repro_parser.py 輸出第 1-2 行）。
      - 機制重現：指令 `cd backend && uv run python ...\scratchpad\audit\work\verify-f8\repro_parser.py`（mock HTTP，沒有連網）。target_date=2026-09-24 與 2026-10-09 時，輸出的 date 全是請求日期，數值與 9/23 相同（2330 收 2500、2454 收 5185、6488 量 28,714,838、8069 金額 841,677,554）；請求 URL 為 dailyQuotes?date=115/09/24。
      - 觸發條件沒有重現：check_backfilled_dates.py 的結果。9/11、9/14 分區寫入時間是 2026-09-16 11:21:37／38（當時最新已公布日是 9/15）。6488 在 9/11 本地量 4,744,408、官方約 4,744,000，收 902；在 9/14 本地 4,179,894、官方約 4,180,000，收 900，都相符。官方 9/15 約 7,583,000，與兩者都不同。全市場上櫃同收盤且同量：9/11 對 9/15 為 0/993、9/14 對 9/15 為 1/994。
      - 錯日掃描：scan_partitions.py，661 個分區，週末分區 0 個；2024-08-01～2026-09-17 間 519 個分區不在加權指數日曆的有 0 個，反之也是 0 個；相鄰日同 OHLCV 比例最高 TPEX 0.0030（9/01→9/02，3/1001）、TWSE 0.0025。9/22 上櫃 6488（本地 18,780,685、官方約 18,781,000）與 8069 都相符。
      - manifest 出處：global_limitations 那句引用 backend/app/taiwan/index_daily.py:29，原文是 "# TPEx silently returns the current month unless the date carries a day component."，講的是月指數端點 inx，不是 dailyQuotes；manifest 也寫「本次 9/23 的請求無法用回應日期分辨這種情況」。
      - 既有記錄：docs/code-review-2026-09-22.md:671-674（N4）實測 TPEx 對未來日期回 stat=ok、日期與請求相同、資料為空，而且只涵蓋 enrichment/margin.py、institutional.py，沒有提到 snapshot_provider.py。
      - 觸發路徑：backend/app/jobs/daily_pipeline.py:180,198 排程週一到週五 16:30／21:40；backend/app/taiwan/realtime/calendar.py:73-79 平日未知日回 None；backend/app/taiwan/daily_refresh.py:161 只略過 is False 的日期，:167 已有分區就略過（誤標之後不會再修正）。
      - 近期休市日：https://www.hankexploring.com/taiwan-and-us-stock-closed-time/ 列出 2026「9/25 (Friday) 中秋節」「9/28 (Monday) 教師節」「10/9 國慶日補假」「10/26 光復補假」休市。
      - 第三方用法：https://github.com/wind750/industrial_flow 使用 dailyQuotes?date={民國年/MM/DD}&response=json，與本地實測「民國格式有效」一致；網路上查不到 dailyQuotes 對休市日的回應行為（推測：與 N4 實測的 TPEx 端點一樣回空資料）。
      - 另案旁證（非 F8）：scan_partitions.py 顯示 2026-09-16、2026-09-23 分區的 tpex=0、twse=1330；data\backend.log:37258「2026-09-23 16:30:05 TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)」，21:40 補跑（backend.log:45470）後仍缺。check_backfilled_dates.py 顯示 16:30 寫入的上市量與官方不符，例如 2454 在 9/18 本地 9,930,705、官方 10,365,174，在 9/23 本地 6,425,491、官方 6,430,491；9/23 上市 1,330 列中有 23 列量不符。

## 確認正確的項目

- **上市個股日K開高低收（TaiwanDailyStore）：自選 9 檔＋2330 共 10 檔，2026-08-03～09-23 共 380 列**：polars 直接讀 data/taiwan/daily 分區，與官方 STOCK_DAY 逐列 join（c1_store_vs_official.py）。open/high/low/close 最大絕對差 0.0，380 列沒有缺列。成交量／金額的差異另見 findings 的 F4
  - 來源：AUDIT_SCRATCH/reference/csv/twse_stock_day_202608_202609.csv
  - 來源：https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY（manifest 下載）
  - 來源：scratchpad\audit\work\A01\c1_store_vs_official.py
- **9/23 上市全市場收盤（抽樣要求 ≥50 檔，實際全數 1,330 檔）**：store 的 date=2026-09-23 與 MI_INDEX 9/23 逐檔 join：OHLC 不一致 0 檔。另依代號排序每隔一段抽 55 檔列出收盤，最大差 0.0（例：00400A 15.57、1101 25.65、2303 160.0、2379 761.0、8210 892.0、9906 39.95）。7 檔 close 為 null（00625K、1538、6952 等），官方同樣是無收盤價（只有零股或完全無成交）（c4_fullmarket_0923.py）
  - 來源：reference\csv\twse_mi_index_20260923_quotes.csv
  - 來源：scratchpad\audit\work\A01\c4_fullmarket_0923.py
- **9/22 全市場收盤（上市＋上櫃）**：以 9/23 官方「收盤−漲跌」反推 9/22 收盤，再與 store 的 date=2026-09-22 比對：上市 1,317 檔、上櫃 965 檔都是 0 不一致（c9_prevclose.py）。這同時證明 snapshot_provider 請求 TPEx 時用的民國日期格式 115/09/22 取得的是正確日期的資料
  - 來源：reference\csv\tpex_daily_quotes_20260923.csv
  - 來源：reference\csv\twse_mi_index_20260923_quotes.csv
  - 來源：scratchpad\audit\work\A01\c9_prevclose.py
- **上櫃個股 6488、8069 日K（2026-08～09-22）**：與 TPEx tradingStock 比對：開高低收 72 列全部吻合（最大差 0.0）。量與金額在張／千元四捨五入 ±500 以內；只有 9 列的 store 值大於官方的張數×1000，原因是 dailyQuotes 含鉅額、tradingStock 註明「不含上櫃股票鉅額交易」，屬於口徑差異而不是錯誤。9/16、9/23 缺列另見 F1
  - 來源：reference\csv\tpex_trading_stock_202608_202609.csv
  - 來源：scratchpad\audit\work\A01\c1_store_vs_official.py
- **單位（股／元）與四個解析器的欄位對應**：把官方原始 JSON 透過 stub client 餵給 app 的解析器（不連網、不寫檔，c10_parsers.py）：MI_INDEX 解析 1,380 列、dailyQuotes 解析 11,696 列，收盤、量、金額都是 0 不一致。STOCK_DAY 逐檔解析（2454，17 列）也是 0 差異，欄位為 日期／成交股數／成交金額／開／高／低／收（backend/app/taiwan/providers/official_provider.py:128-135）。TPEx tradingStock 的「成交張數」「成交仟元」都乘以 1000（official_provider.py:133-134）。volume 一律為股、amount 為元，符合 docs/taiwan-market-contract-v1.md:19 的「lot counts must be converted to shares」
  - 來源：backend/app/taiwan/providers/snapshot_provider.py:161-210
  - 來源：backend/app/taiwan/providers/snapshot_provider.py:242-286
  - 來源：scratchpad\audit\work\A01\c10_parsers.py
- **enriched（kline_daily_enriched）與正本一致、close 與 raw_close 的關係**：2026-06-01 起 enriched 184,553 列與 store join，open/high/low/close/volume/amount 不一致 0 列；raw_close≠close 0 列。store 中 1,828 列 null 收盤（無成交）不進 enriched：filter_halt_days 的 null 比較會把它們濾掉（backend/app/indicators/pipeline.py:987、1010），屬正確行為（c3_enriched_vs_store.py）。raw_close 相等是因為台股沒有傳入復權因子，這是否合理見 F2
  - 來源：backend/app/indicators/pipeline.py:1016-1025
  - 來源：scratchpad\audit\work\A01\c3_enriched_vs_store.py
- **prev_close（非除權息日）**：956 個 TWT49U 事件日的 app prev_close 與官方「除權息前收盤價」比對，0 不一致（c7_exdiv.py），可見 shift(1) 取到的就是官方前一交易日收盤。除權息日本身的口徑問題見 F2
  - 來源：backend/app/indicators/pipeline.py:388
  - 來源：reference\csv\twse_twt49u_20260601_20260923.csv
- **指數日K（加權指數 t00.TWSE、櫃買指數 o00.TPEX）**：data/taiwan/index_daily 在 2026-08～09-17 與官方 MI_5MINS_HIST、TPEx inx 各 34 天比對，開高低收最大差 0.0（c8_index.py）。欄位順序 日期／開／高／低／收 與 _rows_to_frame 的 row[0..4] 一致（index_daily.py:104-123），日期是西元格式，解析正確。9/18～9/23 尚未抓取，屬已知的 B8/B10 延遲抓取
  - 來源：backend/app/taiwan/index_daily.py:104-154
  - 來源：reference\csv\index_daily_202608_202609.csv
  - 來源：scratchpad\audit\work\A01\c8_index.py
- **ETF 價格（0050、00878、00885、00893）**：包含在 380 列的 STOCK_DAY 比對中，OHLC、量、金額都完全吻合（這 4 檔不在成交量差異清單中）
  - 來源：reference\csv\twse_stock_day_202608_202609.csv
- **第三方比對（Yahoo 奇摩股市，9/23 14:30）**：2454：成交 5,185、昨收 5,180、開 5,220、高 5,265、低 5,140；6196：549／535／538／558／534；00878：35.09／35.21／35.30／35.36／35.08。三檔都與 store 的 9/23 OHLC 及 9/22 收盤一致。成交量口徑不同：Yahoo 顯示盤中整股張數（2454 為 5,909 張），app 用官方含零股、盤後、鉅額的成交股數（2454 為 6,425,491 股，官方 6,430,491 股），兩邊不能直接對數
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW
  - 來源：https://tw.stock.yahoo.com/quote/00878.TW
- **檔位與漲跌停取整公式（enriched_refresh._tick_size_expr／_add_limit_signals 的取整部分）**：獨立重寫同一公式（漲停向下取整到檔位、跌停向上取整，檔位依限價所在級距），再套用到官方除權息參考價：956 個事件中漲停與跌停同時吻合 871 個。其餘 85 個有 62 個是官方標示無漲跌幅的 ETF（9999.95／0.01），23 個是含現金增資的除權特例。另外 MIS 快照 5 檔的 u／w 也都能重現，例如 2454：5180→5695／4665；6488：971→1065／874。檔位級距與 docs/taiwan-market-contract-v1.md:26-28 一致
  - 來源：backend/app/taiwan/enriched_refresh.py:117-128
  - 來源：backend/app/taiwan/enriched_refresh.py:169-175
  - 來源：reference\csv\twse_twt49u_20260601_20260923.csv（官方 limit_up／limit_down 欄）
  - 來源：scratchpad\audit\work\A01\c7_exdiv.py
- **volume_units.py（台股量的單位與換手率公式）**：volume_shares_expr 對 .TWSE／.TPEX 保留股數，A 股則乘 100（volume_units.py:19-25）；turnover_rate_pct_expr = 股數×100／float_shares（volume_units.py:28-30）。實算 enriched 9/23 的 2454：6,425,491／1,604,952,613×100 = 0.400354，與存檔值 0.400354 相同；2330 為 0.084755，也相同。分母 issued_shares 與官方 t187ap03_L 的已發行普通股數一致（2454 為 1,604,952,613）。成交量偏低對換手率的影響見 F4
  - 來源：backend/app/volume_units.py:19-30
  - 來源：reference\csv\twse_t187ap03_L_share_capital.csv
- **data_integrity 的時區與收盤判定**：CN_TZ 是固定 UTC+8（app/market_time.py:14），與台北時間相同、沒有夏令時，不會造成偏移；CLOSE_CUTOFF = REGULAR_OPEN_END = 13:30（app/taiwan/realtime/calendar.py:23），符合台股普通交易時段收盤時間。規則覆蓋範圍的問題見 F5
  - 來源：backend/app/services/data_integrity.py:35
  - 來源：backend/app/market_time.py:14
- **daily_store 的去重、讀取與原子寫入**：讀程式碼：寫入依 (symbol,date) unique keep=last，並在每分區鎖內 merge，最後用暫存檔 replace（daily_store.py:97-155）；讀取端只 glob date=*/part.parquet（daily_store.py:209、250），不會讀到 .parquet 暫存檔。實查 661 個分區，除了 part.parquet 之外沒有其他檔案（c2_coverage.py 的 other_files 全空）
  - 來源：backend/app/taiwan/daily_store.py:97-155
  - 來源：scratchpad\audit\work\A01\c2_coverage.py

## 無法驗證的項目

- **上櫃 9/23 的 store 價格**：store 沒有 9/23 的上櫃資料（見 F1），沒有東西可比。只能改為驗證解析器：把官方 dailyQuotes 原始 JSON 餵給解析器，結果 0 差異
- **上櫃個股的除權息事件（F2／F3 對上櫃的影響程度）**：參考資料只有上市的 TWT49U，沒有下載上櫃的除權息表。上櫃走同一段程式碼，依程式碼推定同樣受影響，但沒有逐筆數據
- **當日抓取的成交量短少，實際由哪種交易構成**：9/23 的 23 檔短少中有 22 檔是整張數，推測是鉅額交易（交易時段 9:00～17:00）。但 9/18 的抓取時間已是 17:03，2330 仍少 3,702,177 股；Yahoo 報導的 2330 9/18 盤後定價約 1.48 億元、成交價 2,460 元，約 60 張，解釋不了這個缺口。沒有鉅額交易明細（證交所 BFIAUU 不可連線），因此無法拆解。只能確認「當日抓取偏低、隔日抓取正確」這個事實
- **9/16 上櫃抓取失敗的原因**：9/16 當天的 log 已輪替（只剩 backend.log、.1、.3），只能從分區修改時間 16:30:22 與 TPEx 0 列推定與 9/23 同型。9/23 的原因已由 backend.log:37258 確認
- **official_provider.fetch_quote（STOCK_DAY_ALL、tpex_mainboard_quotes）的欄位與單位**：參考資料沒有下載這兩個 openapi 端點，又不得直連交易所。這條路徑只在 hybrid 的即時備援時使用
- **FinMind、Yahoo 備援 adapter 的單位**：不在下載範圍。hybrid 只有在官方 adapter 失敗時才使用，本地資料中沒有這兩個來源的列可以驗證
- **指數 9/18～9/23**：store 尚未抓取，屬已知 B8／B10 的延遲抓取，不重複回報
- **盤中即時K（MIS v，張×1000）的成交量口徑**：屬於即時行情領域。manifest 已記錄 MIS v 只算盤中整股（例如 2454 為 5,902 張，官方 6,430,491 股）。收盤後 enriched 由 16:30 發佈覆蓋；盤中數值不在本領域比對範圍
- **9/24 00:39～01:23 log 中的 integrity 修復任務紀錄，是測試寫入還是真實啟動**：已知 N2（測試寫進正式 log），單看 log 無法區分。F5 改以唯讀呼叫 scan_recent_integrity(真實 data, today=9/24) 直接證明
