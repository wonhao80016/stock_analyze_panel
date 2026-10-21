# A06-turnover-mcap-pct｜換手率、市值、股本、漲跌幅、振幅、成交值與顯示單位

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 7 | 5 | 0 | 0 | 0 | 12 | 7 |

## 稽核者摘要

結論：公式本身大致正確，錯誤出在資料與口徑。
（1）換手率公式與股本分母都正確：股本 1,988 檔與官方逐檔完全一致；9/23 在成交量正確的前提下，1,066 檔換手率與獨立計算完全相同。第三方 MoneyDJ 週轉率排行有 18/23 檔差距在 ±0.006 個百分點以內。
（2）市值 = 收盤價 × 已發行普通股數：2454 為 8.3217 兆、6196 為 1,209.6 億。Yahoo 用前一日收盤價計算，所以落後一天（6196：535 × 220,329 張，完全對得上）。
（3）非除權息日的漲跌幅、漲跌額、振幅：10 檔 × 37 日共 366 列與獨立計算、官方漲跌價差完全一致，也和 Yahoo 顯示的 2454、6196 數值一致。

主要問題：
- P1｜台股 enriched 完全沒做除權息調整：除權息日漲跌幅以原始前一日收盤價計算，例如 2379 在 9/08 顯示 −5.08%（應為 −1.70%），3545 在 8/04 正負號相反。
- P1｜16:30 排程早於鉅額交易結束時間（17:00），抓到的成交量與成交值偏少，而且之後永不重抓：9/23 有 23 檔偏少，其中 6464 少了 99%。
- P1｜上櫃抓取失敗時仍寫入只有上市的分區，並標為成功：9/16 與 9/23 整個上櫃市場缺日，隔日的漲跌幅變成兩日漲跌。
- P2｜自選頁與盤中的換手率分子用 MIS 盤中整股量：2454 為 0.368%，官方為 0.401%。
- P2｜非交易日重新發佈時，會把最近一個交易日的換手率清空：9/25 中秋休市當天就會觸發。
- 其餘 P3：股本刷新時一個交易所失敗就整檔覆寫、分母含私募股、TDR 分母錯、ETF 無換手率、以「萬億」而非「兆」顯示、成交量以股而非張顯示、自選頁振幅與成交額恆為「—」。

本次共發出 22 次網路請求，都沒有連線證交所或櫃買中心。腳本與輸出放在 AUDIT_SCRATCH/work/A06/（compare1～4、inspect1～2 的 .py 與 .out），沒有寫入 repo 或 data。

## 問題清單

### [P1 → P1] 台股 enriched 未做除權息調整：除權息日漲跌幅、漲跌額、振幅以原始前一日收盤價計算（確認成立）

- **主張**：TaiwanEnrichedRefreshService 呼叫 compute_enriched 時傳入空的 factors，因此台股 close 是未還原的原始價；change_pct = close/close.shift(1) − 1 在除權息日以除息前收盤為基準，而不是交易所的除權息參考價（平盤價）
- **正確的定義或數值**：除權息日以參考價為基準：2379 在 9/08 為 −12 元（−1.70%），振幅 = (721 − 680) ÷ 704 = 5.82%
- **app 實際行為**：app 顯示 −37 元（−5.08%），振幅 5.62%；3545 在 8/04 與 2330 在 9/16 的漲跌方向與交易所相反
- **對使用者的影響**：自選中的 2379、3545、00878 在除權息日的歷史K棒漲跌、資訊條、策略評分（例如 high_turnover_surge 的 change_pct 權重 0.3）、大盤漲跌家數與漲跌分布都會出錯。7～9 月除權息旺季，每天有上百檔受影響。盤中 MIS 以參考價計算，16:30 發佈後同一天的數字會翻成另一個值。MA、MACD 等指標也同樣未還原（屬其他領域）
- **修法建議**：以 TWT49U（上市）與上櫃除權息表的參考價建立台股 adj_factor，並傳給 compute_enriched；至少在除權息日以參考價作為 prev_close 計算 change_pct 與 amplitude
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95 compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
  - backend/app/indicators/pipeline.py:1023（factors 為空時略過前復權）
  - backend/app/indicators/pipeline.py:503-517（change_pct、change_amount、amplitude 以 close.shift(1) 為基準）
  - CONTRIBUTING.md:100（契約寫明 enriched 的 OHLC 為前復權）
  - backend/app/taiwan/market_intelligence.py:403（大盤漲跌家數與漲跌分布也用原始前一日收盤）
  - backend/app/taiwan/watchlist_enrichment.py:116-134（日K 備援路徑同樣問題）
- **線上證據**：
  - https://tw.stock.yahoo.com/news/%E9%99%A4%E6%AC%8A%E6%81%AF%E5%8F%83%E8%80%83%E5%83%B9%E6%98%AF%E4%BB%80%E9%BA%BC%EF%BC%9F%E5%A6%82%E4%BD%95%E8%A8%88%E7%AE%97%EF%BC%9F-074144462.html：除權息參考價是除權息交易日當天扣掉股利價值後的「平盤價」
  - https://news.cnyes.com/news/id/6599425 標題：「瑞昱(2379)次交易(8)日除息25元，參考價704.0元」
  - https://xshelp.xq.com.tw/XSHelp/?HelpName=%E6%8C%AF%E5%B9%85&group=FPRICE：「振幅 = (當期最高價 − 當期最低價) × 100 / 參考價%」
- **本地計算證據**：
  - uv run python compare3.py → 除權息日（TWT49U 參考價）：2379 在 2026-09-08 收盤 692，app change_pct −0.050754，以參考價 704 計算為 −0.017045；3545 在 2026-08-04 app −0.003992，應為 +0.012581（正負號相反）；2330 在 2026-09-16 app −0.002096，應為 +0.000845（正負號相反）；00878 在 2026-08-18 app −0.040593，應為 −0.010996
  - 同一批 366 個非除權息日列差異為 0，證明問題只出在除權息日
  - 官方 STOCK_DAY 在除權息日的漲跌價差為 X0.00（不比價），例如 2379 的 9/08、2330 的 9/16
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 以空的 factors 呼叫 compute_enriched（enriched_refresh.py:95），OHLC 未還原，違反 CONTRIBUTING.md:100 的前復權契約。change_pct、change_amount、amplitude 與 prev_close 在除權息日以除息前收盤為基準（pipeline.py:388-391、503-517）。

但依營業細則第67條，除權息交易開始日的升降幅度與平盤價以參考價為基準；交易所盤後資料（STOCK_DAY、MI_INDEX）對該日標「X 不比價」，漲跌家數也列入「無比價」。app 的數值兩種慣例都不符。

使用者自選 9 檔中有 7 檔在 2026 年 6～9 月除權息，已實查 6196、2454、0050、3545、00878、2379 的歷史 change_pct 錯誤，6196 與 3545 的正負號相反。

新面向：_add_limit_signals（enriched_refresh.py:171-194）與 market_intelligence 的漲跌停判定也用除息前收盤，已存資料有 4 筆除權息日跌停誤判（5906 在 9/10、6924 在 9/15、1442 與 2542 在 9/23）。

規模修正：上市單日最多約 61 檔（7/16），6/01～9/23 合計 994 筆，並非每天上百檔。2330 在 9/16 若以開盤競價基準 2380 計算為平盤，不一定是正負號相反。

修法：除權息日以 TWT49U 及上櫃除權息表的參考價作為 prev_close，計算漲跌、振幅與漲跌停價；大盤家數把除權息股歸入無比價；或建立台股 adj_factor 並傳入 compute_enriched。
    - 理由：這條 finding 的主張成立，嚴重度 P1 恰當。有幾處影響描述需要修正，另外找到一個原記錄沒提到的新面向：除權息日的跌停訊號會誤判。

一、程式碼（依現況行號，親自讀過）
- enriched_refresh.py:95 以 factors=pl.DataFrame() 呼叫 compute_enriched。pipeline.py:1023-1024 在 factors 為空時略過 _apply_adj_factor，因此台股 enriched 的 OHLC 就是原始價。
- pipeline.py:388-391 的 prev_close、:503-504 的 change_pct、:507-510 的 change_amount、:511-517 的 amplitude，全部以 close.shift(1) 為基準。
- 這違反 CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」。
- 背景：hybrid_provider.py:31 設了 adj_factor=False，:151 寫「Phase 2 records raw price semantics」，各 provider 也都標 PriceSemantics.RAW。可見 provider 層刻意使用原始價，但 docs/ 與 code-review-2026-09-22.md 都沒有記錄這對除權息日漲跌幅的影響，因此不算已知問題。

二、正確定義（另找的權威來源，不同於 finding 引用的來源）
- 臺灣證券交易所營業細則第67條第2項（SELAW 證券暨期貨法令判解查詢系統）：「除息交易開始日升降幅度之計算，以前一日收盤價格減除股息及紅利金額為計算之基準。」
- 玉山證券：除權息交易日的平盤價格就是除息參考價。
- 官方資料交叉印證：TWT49U 的漲跌停價是由參考價推出。例如 2379：704×1.1=774.4，取到檔位為 774；704×0.9=633.6，取到檔位為 634。
- 要注意，交易所盤後正式資料不發布除權息日的漲跌。STOCK_DAY 與 MI_INDEX 標 X（註解原文「+/-/X表示漲/跌/不比價」）。MI_INDEX 的漲跌家數表另設「無比價」一類，9/23 股票欄有 5 檔。
- 所以「正確值」有兩種慣例：看盤系統以參考價計算，交易所盤後正式資料則標「不比價」。app 以除息前收盤計算的值，兩種慣例都不符。

三、本地重現（唯讀 parquet）
- finding 的數字完全一致。2379 在 9/08：app 的 change_pct −0.050754、change_amount −37、amplitude 0.056241；以參考價 704 計算為 −0.017045、−12、0.058239。
- 3545、2330、00878 也與 finding 所列一致。

四、使用者情境
- 自選 9 檔中有 7 檔在 2026 年 6～9 月除權息（TWT49U）。已在 enriched 實查錯誤的有：
  - 6196 在 6/24：app −0.18%，以參考價為 +1.02%，正負號相反。
  - 2454 在 7/07：app −2.30%，應為 −1.72%。
  - 0050 在 7/21：app +3.33%，應為 +3.96%。
  - 另有 3545、00878、2379。
- 3034 本地缺 7/10 分區，原因未查，不在本 finding 範圍。
- 盤中 MIS 以 y 欄位為 prev_close（mis_provider.py:160）。y 在除權息日是參考價，這點屬於有依據的推論，今天沒有自選股除權息，無法實測。

五、新面向：除權息日跌停誤判（原記錄與 finding 都沒提）
- enriched_refresh.py:171-194 的 _add_limit_signals 以 prev_close（即除息前收盤）乘以漲跌幅計算漲跌停價。
- 6～9 月已存 enriched 中，除權息日被標 signal_limit_down=True 的 4 列全部是誤判：

  | 代號 | 日期 | 收盤 | 官方跌停價 |
  |---|---|---|---|
  | 5906 | 9/10 | 37.35 | 35.45 |
  | 6924 | 9/15 | 128.0 | 125.5 |
  | 1442 | 9/23 | 27.8 | 25.9 |
  | 2542 | 9/23 | 39.55 | 37.35 |

- 這違反 CONTRIBUTING.md:102「必須基於原始價及對應交易日規則判斷」。
- market_intelligence.py:398-443 的跌停家數用同一個前收盤計算，同樣受影響（由程式碼推論，未實跑）。

六、需要修正的地方
1. 「每天有上百檔受影響」誇大了。TWT49U（只含上市）在 6/01～9/23 共 994 筆、79 個除權息日，單日最多 61 檔（7/16），平均每個除權息日約 12.6 檔。上櫃未納入參考資料。
2. 2330 在 9/16 是否「正負號相反」取決於基準。以參考價 2377.99 計算為 +0.08%；若以開盤競價基準 2380（已取到檔位）計算為 0.00%，也就是平盤。不論哪一種，app 的 −0.21% 都是錯的。
3. 大盤漲跌家數：交易所的慣例是把除權息股列為「無比價」，不是改以參考價比較。app 則把它們算成下跌，例如 2542 在 9/23 被算成 −13%。

嚴重度：這是錯誤的金融口徑（CONTRIBUTING 第 11 節 P1），而且直接影響自選股的歷史漲跌、策略評分與大盤統計，所以維持 P1。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95：enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/indicators/pipeline.py:1023-1024：if factors is not None and not factors.is_empty(): raw = _apply_adj_factor(raw, factors)，factors 為空時不調整
      - backend/app/indicators/pipeline.py:388-391, 503-517：prev_close、change_pct、change_amount、amplitude 全部以 close.shift(1).over('symbol') 為基準
      - CONTRIBUTING.md:100-102：enriched OHLC 為前復權；漲跌停必須基於原始價及對應交易日規則判斷
      - backend/app/taiwan/providers/hybrid_provider.py:31, 151：adj_factor=False；'Return empty adjustment factors (Phase 2 records raw price semantics)'，刻意使用原始價，但沒有文件記錄這對除權息日的影響
      - backend/app/taiwan/enriched_refresh.py:171-194：_add_limit_signals 以 prev_close（除息前收盤）乘以漲跌幅計算漲跌停價
      - backend/app/taiwan/market_intelligence.py:398-443（前收盤 join 與漲跌停判定）、252-254（漲跌家數）、278-285（漲跌分布）
      - backend/app/taiwan/watchlist_enrichment.py:116-134：日K 備援路徑以 close.shift(1) 為 prev_close
      - backend/app/taiwan/realtime/mis_provider.py:160：盤中 prev_close = item.get('y')
      - https://www.selaw.com.tw/Chinese/LawDetail/LawArticleReference?lawDataid=3636777（臺灣證券交易所股份有限公司營業細則第67條，民國108年9月10日版）：「除息交易開始日升降幅度之計算，以前一日收盤價格減除股息及紅利金額為計算之基準。」
      - https://www.esunsec.com.tw/article/post/41（玉山證券）：「除權息交易日的平盤價格即為『除息參考價』」
      - 官方參考資料 raw/twse_stock_day_2379_202609.json 的 notes：「符號說明:+/-/X表示漲/跌/不比價」；2379 在 115/09/08 的漲跌價差為 'X0.00'
      - 官方參考資料 csv/twse_mi_index_20260923_updown_counts.csv：「無比價」一類，全市場 3,136、股票 5；同日 quotes 中 change_sign 為 X 的有 6 列（00930、1442、1808、2542、5258 等除權息標的）
      - 官方參考資料 csv/twse_twt49u_20260601_20260923.csv：2379 在 2026-09-08 的 pre_ex_close 729、ex_ref 704、limit_up 774、limit_down 634（= 704×1.1、704×0.9 取到檔位）
      - 本地 uv run python check_exdates.py（scratchpad\audit\work\verify_exdiv_def\）：2379 在 9/08 的 enriched change_pct=-0.050754、change_amount=-37.0、amplitude=0.056241，以參考價為 -0.017045、-12.0、0.058239；3545 在 8/04 為 -0.003992 對 +0.012581；2330 在 9/16 為 -0.002096 對 +0.000845；00878 在 8/18 為 -0.040593 對 -0.010996（與 finding 完全一致）
      - 本地 uv run python -c（同目錄）：6196 在 6/24 的 app change_pct -0.001848，以參考價 534.55 為 +0.010195（正負號相反）；2454 在 7/07 為 -0.02303 對 -0.017191；0050 在 7/21 為 +0.033266 對 +0.039554
      - 新面向，本地 limit_signals.py：6～9 月除權息日 enriched 中 signal_limit_down=True 共 4 列，全部是誤判。5906 在 9/10 收 37.35（官方跌停 35.45）；6924 在 9/15 收 128.0（125.5）；1442 在 9/23 收 27.8（25.9）；2542 在 9/23 收 39.55（37.35）
      - 規模修正，本地統計：TWT49U（只含上市）在 6/01～9/23 共 994 筆、79 個除權息日，單日最多 61 檔（2026-07-16），不是『每天上百檔』
      - 修正 2330：TWT49U 在 2026-09-16 的 ex_ref 2377.99、opening_auction_ref 2380.0，收盤 2380；以開盤競價基準計算為 0.00%（平盤），以 ex_ref 計算為 +0.08%
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 沒有做除權息調整。TaiwanEnrichedRefreshService（enriched_refresh.py:95）與台股本地日K API（kline.py:435）都以空的 factors 呼叫 compute_enriched，因此除權息日的 change_pct、change_amount、amplitude 以除權息前收盤價為分母，而不是 TWT49U 的除權息參考價。

一、2026-06-01～09-23 的上市數據：956 筆除權息事件全部受影響。其中 350 筆正負號相反，743 筆誤差 ≥1 個百分點，中位數誤差 2.8 個百分點。除權事件的誤差可達 −66%（例如 6669 在 9/02）。非除權息日的數值正確，376 列比對差 0。

二、自選股受影響的事件：
- 2379，9/08：app −5.08%，應為 −1.70%；振幅 5.62%，應為 5.82%。
- 3545，8/04：−0.40%，應為 +1.26%。
- 00878，8/18：−4.06%，應為 −1.10%。
- 6196，6/24：−0.18%，應為 +1.02%。
- 2454，7/07：−2.30%，應為 −1.72%。
- 0050，7/21：+3.33%，應為 +3.96%。

三、2330 在 9/16 app 顯示 −0.21%。以參考價 2377.99 計算為 +0.08%，以開盤競價基準 2380 計算則是平盤。交易所日資料對除權息日標 X（不比價）。

四、受影響的下游：資訊條、策略篩選與評分、大盤漲跌分布，以及以同一基準計算的漲跌停判斷。依官方漲跌停價比對，23 檔漲停中漏判 22 檔，另有 52 檔被誤判跌停。

五、影響規模：上市每日約 12 檔，旺季單日最多 60 檔。「每天上百檔」不成立；上櫃未驗證。
    - 理由：核心主張完整重現，finding 裡的數值全部一致。台股 enriched 以空的 factors 呼叫 compute_enriched，所以除權息日的 change_pct、change_amount、amplitude 都以除權息前的收盤價為分母，而不是交易所的除權息參考價。

一、本地已發佈資料的重現：
- TWT49U 共 994 筆事件，其中 956 筆能對到 kline_daily_enriched。這 956 筆的 prev_close 全部等於 TWT49U 的除權息前收盤價，close 也全部等於 raw_close（未還原）。change_pct 全部等於 close ÷ 除權息前收盤 − 1。
- 自選與 2330 的數值（左邊是 app，右邊是 close ÷ 參考價 − 1）：
  - 2379，9/08：−5.0754% 對 −1.7045%；漲跌額 −37 對 −12；振幅 5.6241% 對 5.8239%。
  - 3545，8/04：−0.3992% 對 +1.2581%。
  - 2330，9/16：−0.2096% 對 +0.0845%。
  - 00878，8/18：−4.0593% 對 −1.0996%。
  以上與 finding 逐位相同。
- 另外，我用 app 的純函數 compute_indicators 從 taiwan/daily 的原始資料重算，結果與已發佈的 enriched 完全一致（差 0.0）。改成以 TWT49U 建出 ex_factor = 除權息前收盤 ÷ 參考價，經 _apply_adj_factor 後，除權息日的 change_pct 與 amplitude 正好等於以參考價計算的值。可見修法方向可行。

二、非除權息日沒有問題：官方 STOCK_DAY 的 10 檔在 8～9 月共 376 列非 X 列，app 的 change_amount 差異為 0，close 差異也為 0。finding 寫 366 列，與我算的 376 列不同，但結論一樣。STOCK_DAY 在 4 個除權息日都標 X0.00（不比價）。

三、需要修正或補充的地方：
1. 「每天有上百檔受影響」不成立（至少上市部分）。上市除權息事件每日最多 60 檔（7/16），平均 12.3 檔，沒有任何一天達到 100 檔；9 月只有 102 筆，分布在 14 天。上櫃不在參考資料內，無法驗證。
2. 2330 在 9/16 的「方向相反」取決於採用哪個參考價。以 TWT49U 未進位的參考價 2377.99（鉅亨寫 2378.0）計算是 +0.08%。以開盤競價基準 2380 計算則是平盤 0%。無論哪一種，app 的 −0.21% 都是錯的。交易所日資料本身標 X（不比價），並未公布方向。
3. finding 少列了受影響的自選股：
   - 2454（7/07）：−2.30% 對 −1.72%。
   - 6196（6/24）：−0.18% 對 +1.02%，方向相反。
   - 0050（7/21）：+3.33% 對 +3.96%。
   - 3034 的除權息日 7/10 在本地 daily 與 index_daily 都沒有分區，所以息值缺口落在 7/13，單日顯示 −13.75%，超過 ±10% 漲跌幅限制。7/10 是否為交易日未查證（推測）。
4. 規模與加重面向：
   - 956 筆中有 350 筆正負號嚴格相反，743 筆誤差 ≥1 個百分點，誤差中位數 2.82 個百分點。
   - 除權事件誤差特別大，例如 6669（9/02）app 顯示 −66.5%，實際為 −0.19%。
   - 52 筆 app 算出 ≤ −9.5%，但依參考價計算其實 > −9%。
   - 新面向：依我重寫的 enriched_refresh.py:169-175 公式，除權息日的漲跌停判斷同樣以除權息前收盤為基準。與 TWT49U 官方漲跌停價比對，23 檔官方漲停中有 22 檔判不出來，另有 52 檔被誤判跌停。不過目前只有最近 10 個分區帶有 signal_limit_up 欄位。market_intelligence.py:439 的 calc_limits(prev_close) 同樣以原始昨收為基準。

四、其他 user_impact 的查核：
- 「盤中 MIS 以參考價計算」：mis_provider.py:160 以 MIS 的 y 欄當 prev_close，但除權息日 y 的實際值未查證（推測）。
- 資訊條：StockInfoBar.tsx:135-165 直接使用後端最新一列的 change_pct，並以它反推 prev_close 來算振幅，因此確實會顯示錯誤值。
- 台股日K API：kline.py:435 同樣是 compute_enriched(local, factors=pl.DataFrame())。

嚴重度維持 P1，屬於錯誤的金融口徑。原因：它違反 CONTRIBUTING.md:100 的前復權契約，影響策略篩選（high_turnover_surge.py:44 對 change_pct 的權重 0.3，:73 用它篩選 >3%）與大盤漲跌統計，但不破壞資料。

工作說明：我的腳本放在 scratchpad 的 verify-exdiv\ 目錄。該目錄原本已有其他 agent 的檔案（exdiv_verify.py 等），我的 Write 可能覆寫了一個同名的舊 verify.py。repo 的 git status 與開始時相同，沒有任何修改。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95：compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/indicators/pipeline.py:1023-1024：factors 非空才呼叫 _apply_adj_factor；pipeline.py:503-516：change_pct、change_amount、amplitude 都以 close.shift(1) 為分母
      - CONTRIBUTING.md:100：「enriched 的 open/high/low/close 為前復權價格」
      - verify.py 輸出（scratchpad\audit\work\verify-exdiv\verify_out.txt）：TWT49U 994 筆，對到 enriched 956 筆；prev_close 等於除權息前收盤 956/956；close 等於 raw_close 956/956；app change_pct 等於 close ÷ 除權息前收盤 − 1 共 956/956
      - 同一輸出的自選事件，左邊是 app，右邊是以參考價計算：2379 9/08 −0.050754 對 −0.017045，漲跌額 −37 對 −12.0，振幅 0.056241 對 0.058239；3545 8/04 −0.003992 對 +0.012581；2330 9/16 −0.002096 對 +0.000845（以開盤競價基準 2380 計算則為 0.0）；00878 8/18 −0.040593 對 −0.010996；6196 6/24 −0.001848 對 +0.010195；2454 7/07 −0.02303 對 −0.017191
      - 同一輸出的規模統計：正負號嚴格相反 350/956；誤差 ≥1 個百分點 743 筆；誤差中位數 2.8237 個百分點；上市每日除權息事件最多 60 筆（7/16），平均 12.3 筆，≥100 筆的天數為 0
      - 同一輸出的 STOCK_DAY 比對：非 X 列 376 列，change_amount 差異 0，close 差異 0；2330 9/16、2379 9/08、3545 8/04、00878 8/18 的 change_raw 都是 X0.00
      - verify2.py（compute_indicators 重算）：已發佈 enriched 與空 factors 重算結果的 max|d change_pct| = 0.0，max|d amplitude| = 0.0；以 TWT49U 的 ex_factor 重算時，fix_change_pct 與 fix_amplitude 等於以參考價計算的值（例如 2379 為 −0.017045 與 0.058239）
      - verify3.py：6669 在 9/02（除權）app change_pct 為 −0.665385，以參考價計算為 −0.001908；app ≤ −9.5% 但依參考價 > −9% 的有 52 筆；3034 的 7/10 在 taiwan/daily 與 index_daily 都沒有分區，7/13 單日 app 顯示 −0.137454
      - verify4.py（依 enriched_refresh.py:169-175 公式重寫）：官方漲停 23 檔中 app 漏判 22 檔；誤判跌停 52 檔。目前只有 10 個 2026 分區帶 signal_limit_up 欄位
      - https://news.cnyes.com/news/id/6599425：「瑞昱(2379)次交易(8)日除息25元，參考價704.0元」、「首日參考價為704.0元，相較今日收盤價729.00元」
      - https://news.cnyes.com/news/id/6606965（搜尋摘要）：台積電 9/16 除息參考價 2378.0 元，相較收盤價 2385.00 元
      - backend/app/taiwan/market_intelligence.py:403 以原始昨收當 prev_close；:439 用它算漲跌停價
      - backend/app/taiwan/watchlist_enrichment.py:117、:128-133 的日K 備援同樣以 shift(1) 原始昨收計算漲跌
      - backend/app/api/kline.py:435 的台股本地日K 也用 compute_enriched(local, factors=pl.DataFrame())；frontend/src/components/StockInfoBar.tsx:135-165 資訊條直接用最新一列的 change_pct 反推 prev_close 並計算振幅
      - backend/app/strategy/builtin/high_turnover_surge.py:44 對 change_pct 的權重為 0.3；:73 以 change_pct > 3% 篩選

### [P1 → P1] 上櫃抓取失敗時仍寫入只有上市的分區並回報 success，之後永不補抓（9/16、9/23 整個上櫃市場缺日）（確認成立）

- **主張**：fetch_date 在 TPEx 失敗時只回傳上市資料；refresh_dates 照樣寫入該日分區並把日期視為已完成；21:40 與之後的更新都略過這一天，而且不會重試上櫃
- **正確的定義或數值**：任一交易所抓取失敗時，該日期不應視為完成，之後的排程應重試；隔日漲跌應以真正的前一交易日為基準
- **app 實際行為**：上櫃約 1,000 檔永久缺 9/16 與 9/23 的日K，隔日漲跌幅與振幅變成兩日值
- **對使用者的影響**：所有上櫃個股的日K、當日換手率、隔日漲跌、指標與策略都受影響；Dashboard 的大盤成交額與漲跌家數在缺日當天只剩上市
- **修法建議**：fetch_date 任一交易所失敗就回報該日失敗、不寫分區（或逐交易所記錄完成狀態）；可用性檢查改看兩個市場都有資料；計算漲跌時確認前一列就是前一交易日
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:113-123（TPEx 例外只記 warning，照樣 concat）
  - backend/app/taiwan/daily_refresh.py:167-177
  - backend/app/indicators/pipeline.py:503-504（以「列」而非交易日 shift，缺日時變成跨兩日的漲跌）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - inspect2.out：data/taiwan/daily 在 2026-09-16 與 2026-09-23 的 TPEX 列數為 null（其他日約 1,005～1,009 列）
  - backend.log：2026-09-23 16:30:05 WARNING TPEx snapshot fetch failed for 2026-09-23: peer closed connection ... (incomplete chunked read)，隨後 16:30:33 daily=success；21:40:10 daily=success，沒有重抓
  - compare3.out：6488 在 9/17 的 app change_pct 為 −0.002188（對 9/15 的 914），官方為 −0.026681（對 9/16 的 937）；8069 app 為 −0.003425，官方為 −0.016892；振幅也以錯的基準計算
  - 9/23 大盤成交額（market_intelligence 加總 amount）只有上市 8,872.5 億，完全沒有上櫃（官方 totalTradingAmount 2,517.9 億）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：OfficialDailySnapshotAdapter.fetch_date 在任一交易所失敗（例外，或回傳空表）時，只回傳另一個交易所的資料。refresh_dates 照樣寫入該日分區並算成 fetched，daily 狀態為 success。之後 run_update、update_to_latest、bootstrap sync 都只看最大分區日期，連 refresh_dates 都不會再呼叫，所以 21:40 班與之後的排程不會補抓，只能從 CLI 用 --force --target-date 手動修。已實際發生：2026-09-16 與 2026-09-23 的上櫃約 1,000 檔整日缺K（9/23 有 log 證實是 incomplete chunked read；9/16 的 log 已輪替，原因屬推測）。因為 pipeline 以列 shift，缺日隔天的漲跌幅、漲跌額、振幅都以兩天前收盤為基準（例：6488 9/17 app -0.22%，官方 -2.67%）。缺日當天 Dashboard 的成交額與漲跌家數只剩上市，隔天的漲跌家數也把上櫃算成 uncompared；資料狀態仍顯示 current。使用者的 9 檔自選都是上市股、監控規則 0 條，所以不受直接影響，但全市場篩選、策略、產業與大盤統計都受影響。既有測試 test_failure_isolation_does_not_crash_or_mark_holiday 把這個行為寫成預期，修正時要一併修改。
    - 理由：我親自讀了程式碼、本地資料、log 和官方參考資料，finding 的主張全部成立。

1) 程式碼：fetch_date 對 TPEx 例外只記一筆 warning，照樣回傳只有上市的資料（snapshot_provider.py:113-124）。refresh_dates 只要 df 非空就寫入該日分區，並算進 dates_fetched，不會列進 failed_dates（daily_refresh.py:172-177），所以 run_update 把 daily 判成 success（daily_update.py:303-309）。

2) 永不重試，而且比 finding 講的更根本：run_update 的缺日範圍 d_range 由 resolve_missing_date_range(d_max, target) 決定，只看最大分區日期（daily_update.py:257,287）。該日分區一旦存在，d_range 就是 None，daily 直接回報「Daily store already current」，連 refresh_dates 都不會呼叫。bootstrap.update_to_latest 與 post-bootstrap sync 也只看 latest date（bootstrap.py:392-397,436-448）。即使真的進了 refresh_dates，也會因為 cur in available 而略過（daily_refresh.py:167）。自動路徑沒有任何補抓機制，前端也沒有 force 入口，只能從 CLI 用 --force --target-date 手動修復。daily_pipeline.py:194-195 的註解寫明 21:40 那班是要讓「16:30 那班若失敗仍有第二次機會」，但部分失敗剛好會讓這個設計失效。get_freshness 也只看最大日期，所以資料狀態會顯示 current，使用者看不到異常。

3) 本地資料：data/taiwan/daily 的 2026-09-16 與 2026-09-23 分區只有 TWSE 1330 列、TPEX 0 列，其他日 TPEX 有 1005～1009 列；kline_daily_enriched 這兩天的 TPEX 也都是 0 列。backend.log:37258 記錄 2026-09-23 16:30:05 TPEx fetch failed（incomplete chunked read）；37266 顯示 16:30:33 daily=success。21:40 那班（45469-45478）只看得到 share capital 與 enriched 的請求，沒有 MI_INDEX 或 dailyQuotes，確認沒有重抓。9/16 當天的 log 已經輪替掉（backend.log.2 不存在），所以 9/16 的失敗原因只能從分區內容和 mtime 16:30:22 推斷，沒有 log 直接佐證。

4) 數值錯誤已重現：6488 在 9/17 的 enriched change_pct 為 -0.002188，prev_close 為 914（9/15）。官方 tpex_trading_stock 顯示 9/16 收 937、9/17 收 912、漲跌 -25，正確值是 -25/937 = -2.668%。8069 的 app 值為 -0.003425，官方為 -2.5/148 = -1.689%。振幅也用錯基準：app 為 71/914 = 0.07768，正確為 71/937。成因是 pipeline.py:503-504 以列而非交易日做 shift。9/23 的上市成交值加總為 8,872.5 億，TPEX 0 列；官方 TPEx totalTradingAmount 為 251,793,647,150 元。

5) 定義：網路來源（XQ 嘉實、Win 投資）都指出漲跌幅與振幅的分母是參考價，也就是開盤競價基準，通常等於前一交易日收盤價。本地的官方 TPEx 資料也用 9/16 當基準，所以 app 以 9/15 當基準是錯的口徑。

補充 finding 沒提到的面向：(a) tests/test_taiwan_snapshot_daily.py:273-300 把「TPEx 失敗仍寫入並算 fetched」寫成預期行為，修正時要一併改這個測試。(b) 缺日隔天（9/17，以及 9/24 抓到之後）market_intelligence 的 prev_d 是只有上市的那一天（market_intelligence.py:225-230,383-384），上櫃個股會落進 uncompared，所以漲跌家數在缺日的隔天也少了上櫃。(c) TPEx 回傳空表（例如尚未公布）也會走同一條路徑，而且上市失敗、上櫃成功的情況是對稱的。

嚴重度：使用者的 9 檔自選都是 .TWSE、監控規則 0 條，自選與告警不受直接影響。但全市場約 1,000 檔上櫃股會靜默且永久缺日K，隔日漲跌幅、振幅、指標、策略選股，以及 Dashboard 的成交額與漲跌家數都變成錯的口徑，而且狀態顯示 success／current；6 個交易日內已經發生 2 次。這符合 P1「錯誤金融口徑」，還沒到 P0 的程度，因為既有資料沒被覆寫，用 CLI --force 也能修復。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:113-124：TPEx 例外只記 logger.warning，frames 只剩上市資料，照樣 concat 後回傳
      - backend/app/taiwan/daily_refresh.py:167-177：已存在的日期直接略過；df 非空就 write_batch 並 dates_fetched += 1，不會進 failed_dates
      - backend/app/taiwan/daily_update.py:257,287-290：d_range 只由 max(d_dates) 決定，分區存在就回報 'Daily store already current'，不再呼叫 refresh_dates
      - backend/app/jobs/daily_pipeline.py:194-195：註解寫明 21:40 班要給 16:30 失敗第二次機會，部分失敗時這個設計失效
      - backend/app/taiwan/bootstrap.py:436-448：update_to_latest 同樣只看最新日期
      - backend/app/indicators/pipeline.py:503-504,512-515：change_pct、amplitude 以 close.shift(1).over('symbol') 逐列計算
      - backend/tests/test_taiwan_snapshot_daily.py:273-300：測試把只有上市資料仍寫入、dates_fetched==1 寫成預期行為
      - backend/app/taiwan/market_intelligence.py:225-230,383-384：prev_d 取自 available_dates，缺日隔天上櫃股的 prev_close 為 null，被算成 uncompared
      - 本地計算（uv run python scratchpad\audit\work\verify_tpex_gap\count.py）：date=2026-09-16 {'TWSE': 1330}，mtime 2026-09-16T16:30:22；date=2026-09-23 {'TWSE': 1330}，mtime 2026-09-23T16:30:05；其他日 TPEX 1005～1009 列
      - 本地計算（enr.py）：kline_daily_enriched 在 9/16 與 9/23 的 TPEX 列數為 0；6488.TPEX 2026-09-17 change_pct=-0.002188、prev_close=914.0、amplitude=0.077681；8069.TPEX 2026-09-17 change_pct=-0.003425、prev_close=146.0
      - 官方參考資料 csv/tpex_trading_stock_202608_202609.csv：6488 2026-09-16 close 937、2026-09-17 close 912、change -25（正確 -2.668%）；8069 2026-09-16 close 148、2026-09-17 close 145.5、change -2.5（正確 -1.689%）
      - 官方參考資料 raw/tpex_daily_quotes_20260923.json：totalTradingAmount '251,793,647,150'；本地 9/23 分區上市 amount 加總 8872.508 億、TPEX 0 列
      - data/backend.log:37258 '2026-09-23 16:30:05 ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)'；37266 16:30:33 daily=success；45469-45478 21:40 班沒有 MI_INDEX 或 dailyQuotes 請求，daily=success
      - 9/16 的 log 已輪替（只剩 backend.log、.log.1、.log.3，.log.3 起於 2026-09-17 11:14），9/16 的失敗原因沒有 log 佐證，屬推測
      - https://xshelp.xq.com.tw/XSHelp/?HelpName=%E6%BC%B2%E8%B7%8C%E5%B9%85&group=QPRICE：漲跌幅 = '(最新一筆成交價 ─ 參考價)／參考價 ＊ 100'
      - https://xshelp.xq.com.tw/XSHelp/?HelpName=%E6%8C%AF%E5%B9%85&group=QPRICE：振幅 = '(當日最高價 - 當日最低價)／參考價＊100'
      - https://winvest.tw/Knowledge/Article/348：開盤競價基準 '通常等於前一天的收盤價'
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：原敘述經重現全部成立，只補充三點。

1. fetch_date 在 TPEx 抓取失敗（例外或空結果）時仍然回傳只有上市的資料，refresh_dates 照樣寫入該日分區，run_update 記為 success。之後所有更新路徑都從最新分區的下一天開始，這一天的上櫃永遠不會被補抓。
2. 9/16 與 9/23 兩個分區實測上櫃都是 0 列，約 1,006～1,009 檔缺日。9/23 的 log 證實是 TPEx incomplete chunked read；9/16 的 log 已輪替，失敗原因無直接證據，但分區同樣缺上櫃。
3. compute_enriched 以列 shift，所以缺日隔天的 change_pct、change_amount、amplitude 都變成兩日值。例如 6488 在 9/17：app 為 −0.2188%、−2 元，官方為 −2.668%、−25 元。9/23 的大盤成交額只有上市 8,872.5 億，官方上櫃另有 2,517.9 億。
4. market_intelligence 在缺日隔天把上櫃算進 uncompared，不列入漲跌家數，而不是算成兩日漲跌。
5. 使用者的 9 檔自選全是上市，不直接受影響；受影響的是 Dashboard 大盤統計與全市場的選股、策略和指標。
    - 理由：我用本地 parquet 與官方參考資料重新算過，finding 的所有數值都能重現，程式碼路徑也和它說的一致。

1. 缺日：data/taiwan/daily 的 9/16 與 9/23 兩個分區，TPEX 列數都是 0，TWSE 各 1330 列。前後日的 TPEX 列數在 1004～1009 之間（9/15 為 1006、9/17 為 1006、9/22 為 1009）。兩個分區的 mtime 分別是 2026-09-16T16:30:22 與 2026-09-23T16:30:05，都在 16:30 排程寫入，之後沒有再改過。

2. 9/23 的 log：16:30:05 有 `TPEx snapshot fetch failed ... incomplete chunked read`，16:30:33 記錄 `daily=success`。21:40 那一輪完全沒有 MI_INDEX 或 dailyQuotes 請求，同樣記錄 `daily=success`，並且 `is_fully_current=True`。9/16 的 log 已經被輪替掉（backend.log.2 不存在，.3 從 9/17 11:14 開始），所以 9/16 的失敗原因沒有直接證據。不過不論是例外還是空結果，程式碼都會寫入只有上市的分區，結果相同。

3. 不會重試：run_update 用 `resolve_missing_date_range(d_max, target)`，從 d_max+1 開始抓；refresh_dates 會略過已經存在的分區日期；bootstrap 的 update_to_latest 也從 latest+1 開始。沒有任何路徑會回頭補上櫃。目前已過 8 天（9/16）與 1 天（9/23），兩個分區仍然沒有 TPEX 列。

4. 隔日漲跌（讀的是 UI 用的 kline_daily_enriched 9/17 分區實際存值）：
- 6488：app change_pct −0.002188（以 9/15 的 914 為基準），官方是 −0.026681（以 9/16 的 937 為基準）。
- 8069：app −0.003425，官方 −0.016892。
- 振幅 6488：app 0.077681，官方 0.075774；8069：app 0.037671，官方 0.037162。
- 另外找到 change_amount 也錯：6488 app 為 −2.0，官方 −25；8069 app 為 −0.5，官方 −2.5。
- 缺日當天的 enriched 分區裡，TPEX 也是 0 列。

5. 成交額：9/23 分區上市 amount 加總 887,250,849,184（8,872.5 億），上櫃為 0。官方 TPEx dailyQuotes 的 totalTradingAmount 是 251,793,647,150（2,517.9 億）。9/22 本地上櫃為 2,711.1 億，用來對照正常日的量級。

6. 影響面：本地 9/22 的 1009 檔上櫃中，有 1007 檔出現在官方 9/23 的 dailyQuotes；有 1006 檔上櫃同時有 9/15 與 9/17、卻缺 9/16。所以「約 1,000 檔」屬實。

補充兩點：
- market_intelligence 用「前一個可用分區」當 prev_close，所以缺日的隔日（9/17、以及即將到來的 9/24）上櫃的 prev_close 是 null，會被算進 uncompared，不列入漲跌家數，也不會變成兩日漲跌。兩日值只出現在 compute_enriched 這條路徑（以列 shift）。
- 使用者自選的 9 檔全是上市，直接看自選不受影響。受影響的是 Dashboard 大盤成交額與漲跌家數，以及全市場的選股、策略與指標。

嚴重度維持 P1：這是錯誤的金融口徑，而且約 1,000 檔永久缺資料、流程卻回報 success。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-124：TPEx 例外只記 logger.warning，然後把只有 TWSE 的 frames 照樣 concat 回傳；fetch_tpex 沒找到表格時也回傳空 DataFrame（:228-230），結果相同
      - backend/app/taiwan/daily_refresh.py:167-177：已存在的日期略過；df 不為空就 write_batch 並算進 dates_fetched，不檢查兩個市場是否都有資料
      - backend/app/taiwan/daily_update.py:282、302-311 與 :141（resolve_missing_date_range 從 d_max+1 起算）：沒有 failed_dates 就記為 success，以後也不會回頭補
      - backend/app/taiwan/bootstrap.py:447：update_to_latest 同樣從 latest+1 開始
      - backend/app/indicators/pipeline.py:503-517：change_pct、change_amount、amplitude 都用 shift(1).over("symbol")，以列而不是交易日為基準
      - count.py 輸出：2026-09-16 total 1330 TWSE 1330 TPEX 0，mtime 2026-09-16T16:30:22；2026-09-23 total 1330 TWSE 1330 TPEX 0，mtime 2026-09-23T16:30:05；9/15 與 9/17 的 TPEX 各 1006，9/22 為 1009
      - data/backend.log:37258：2026-09-23 16:30:05 WARNING TPEx snapshot fetch failed for 2026-09-23: peer closed connection ... (incomplete chunked read)；:37265 16:30:33 daily=success；:45470-45477 21:40 沒有 MI_INDEX 或 dailyQuotes 請求，daily=success、is_fully_current=True
      - 9/16 的 log 已輪替：backend.log.3 從 2026-09-17 11:14 開始，backend.log.2 不存在，所以 9/16 的失敗原因無法以 log 佐證
      - verify.out（腳本在 scratchpad\audit\work\verify-tpexgap\verify.py）：enriched 9/17 的 6488.TPEX change_pct -0.002188、amplitude 0.077681、change_amount -2.0；官方重算 change_pct -0.026681、amplitude 0.075774（官方 change_twd -25）
      - verify.out：8069.TPEX 9/17 的 app change_pct -0.003425、amplitude 0.037671、change_amount -0.5；官方 -0.016892、0.037162（官方 change_twd -2.5）
      - reference csv\tpex_trading_stock_202608_202609.csv：6488 收盤 9/15 為 914、9/16 為 937、9/17 為 912；8069 為 146、148、145.5
      - verify.out：本地 9/23 上市 amount 加總 887,250,849,184（8872.5 億），上櫃 0 列；官方 raw\tpex_daily_quotes_20260923.json 的 totalTradingAmount 為 251,793,647,150（2517.9 億）；本地 9/22 上櫃為 2711.1 億
      - verify.out：有 1006 檔上櫃同時有 9/15 與 9/17、卻缺 9/16；本地 1009 檔上櫃中，有 1007 檔在官方 9/23 的 dailyQuotes 裡
      - backend/app/taiwan/market_intelligence.py:225-230、383-407：prev_close 取前一個可用分區，缺日隔天上櫃的 prev_close 為 null，會被算進 uncompared（:249-250），不是兩日漲跌

### [P2 → P2] 非交易日或隔日補跑的重新發佈，會把最近一個交易日的換手率清空（確認成立）

- **主張**：排程週一至週五都會執行，沒有休市判斷；daily 已是最新時仍回報 success 並執行 refresh_recent 重算最近 5 個分區；apply_historical_float_shares 以牆鐘時間 cn_today() 判斷，最近交易日 ≠ 今天時換手率被設為 null
- **正確的定義或數值**：一個交易日的當日換手率一經算出，就應保留到下一個交易日
- **app 實際行為**：9/25（休市）16:30 的排程會把 9/24 的換手率清空，並以此重算策略快取，持續到 9/29 第一筆即時報價；隔日補跑的交易日從未有換手率
- **對使用者的影響**：連假期間，K 線資訊條的「換手」消失；以 turnover_rate 評分或過濾的 4～5 個內建策略（high_turnover_surge、bullish_alignment、low_volatility_leader、pullback_to_support 等）結果改變或為空
- **修法建議**：today 改用「目前最新交易日」或股本的 report_date 判斷，而不是牆鐘日期；或在 daily 沒有新資料時略過重新發佈
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/jobs/daily_pipeline.py:180,198
  - backend/app/taiwan/daily_update.py:283-286,376-390
  - backend/app/taiwan/enriched_refresh.py:95,191-198
  - backend/app/indicators/pipeline.py:735
  - backend/app/share_capital.py:39-51
  - backend/app/taiwan/watchlist_enrichment.py:92-93（自選頁在 trade_date ≠ 今天時也是 null）
- **線上證據**：
  - https://www.ctee.com.tw/news/20260917700877-430201：中秋節及教師節連假，台股 9/25（五）與 9/28（一）休市，9/29 恢復交易
- **本地計算證據**：
  - compare3.out：cn_today() = 2026-09-24，對官方 9/22～9/23 資料呼叫純函式 compute_enriched，結果 9/23 的 turnover_rate 全部為 null；9/23 分區在磁碟上目前有值（1,087 檔），只要重新發佈就會被清空
  - compare2.out：date=2026-09-21 分區寫入時間為 2026-09-22 10:55（隔日補跑），推測該日換手率在發佈時就是 null
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：排程週一至週五都會執行。TaiwanTradingCalendar 預設沒有任何假日，平日休市日（9/25、9/28、10/9、10/26 等）會被當成「未驗證的交易日」，16:30 的目標日就是當天。抓到的空快照（連線失敗也一樣）被 refresh_dates 記成 dates_skipped，daily 狀態因此為 success，接著執行 refresh_recent 重新發佈最近 5 個分區。compute_enriched 用牆鐘日期 cn_today() 判斷當日，最近交易日（9/24）的換手率被清成 null，並據此刷新 enriched 快取、重算策略快取。這個狀態會持續到 9/29：StockInfoBar 要到盤中注入即時K棒後恢復，策略快取要到 9/29 16:30 才重算。受影響的是 StockInfoBar 的「換手」（K 線 tooltip 對台股本來就不顯示換手），以及 high_turnover_surge（變成空結果）與 3 個以 turnover_rate 加權評分的策略（排名改變）。「已是最新仍 success 並重新發佈」這條路存在，但只在同日重跑時發生，當天無害。「隔日補跑的交易日從未有換手率」只適用於手動等非排程的補跑，屬推測。另一個新面向：fetch_date 吞掉網路錯誤後回報 success，使 daily_pipeline 的「daily 失敗不重算策略快取」gate 失效。
    - 理由：主張成立：非交易的平日（例如 9/25 中秋、9/28 教師節）16:30 與 21:40 的排程都會把 daily 判成 success，於是重新發佈最近 5 個分區。這時 compute_enriched 用牆鐘日期 cn_today() 判斷「今天」，最近一個交易日（9/24）的換手率因此被設成 null，接著刷新記憶體快取並重算策略快取。以下是我親自讀程式碼、做純函式模擬、讀磁碟資料得到的結果，另外有 4 點要修正或補充。

【機制，已實證】
(1) 排程是 CronTrigger(day_of_week="mon-fri")，沒有休市判斷。TaiwanTradingCalendar 預設沒有任何假日，所以 is_trading_day(2026-09-25) 和 is_trading_day(2026-09-28) 都回傳 None，不是 False。
(2) 需要修正 finding 的機制描述：在休市日當天，走的不是「daily 已是最新」這條路。模擬 9/25 16:30：目標日 = 9/25，缺漏區間 = (9/25, 9/25)，因此會去抓 9/25 的快照。fetch_date 會吞掉例外；休市回應（TWSE stat 非 OK、TPEx 空表）得到空表，refresh_dates 把它記成 dates_skipped，failed_dates 為空，daily.status 就是 success。「已是最新 → success → 重新發佈」這條路也確實存在：9/23 21:40 的 log 顯示 daily=success，並且重新發佈了 10576 rows，但它只出現在同一天重跑，當天不會造成損害。兩條路最後都會在 daily_update.py:376-390 重新發佈，daily_pipeline.py:169 的 gate 也放行，然後重算策略快取。
(3) apply_historical_float_shares 會把 trade_date != today 的台股列的 float_shares 設成 null（share_capital.py:46-50）。用真實日K和股本、只 patch cn_today 測試：cn_today=9/23 時，9/23 的自選股有 5/9 檔有換手率；cn_today=9/24 或 9/25 時變成 0/9（4 檔 ETF 本來就沒有值）。
(4) 磁碟上的分區符合這個模式：9/17、9/18、9/21、9/22 分區在 9/23 重新發佈後，換手率非 null 為 0；9/23 分區有 1087 筆。

【定義】MoneyDJ 財經百科寫的是「週轉率是當日交易的總股數與上市的總股數之比」，代表這是固定屬於某個交易日的值，不會因為隔天休市就失效。程式本身的設計意圖也一樣：enriched_refresh.py:86-89 與 daily_update.py:377-379 的註解都要求最新交易日的分區帶有換手率。fail-closed 規則原本是為了防止把目前股本套用到歷史日期；用牆鐘日期代替「最新交易日」，在平日休市時就失效了。我另外核算了口徑：2454 在 9/23 的 6,425,491 股 ÷ 1,604,952,613 股 = 0.400354%，和 app 的輸出一致，可見口徑本身沒問題，問題只在 null 化。

【修正與補充】
(a)「K 線資訊條換手消失」要限定是 StockInfoBar（N1 修正後讀後端的 turnover_rate，StockInfoBar.tsx:148）。EChartsCandlestick 的 tooltip 用 stockInfo.float_shares 計算，而台股的 float_shares 永遠是 None（kline.py:195），所以那裡本來就不會顯示換手，與休市無關。
(b)「隔日補跑的交易日從未有換手率」只算部分成立：misfire_grace_time=3600，排程最多延後到當天 22:40 執行，不會跨到隔日；只有手動 CLI 或其他路徑在隔天早上補抓，才會出現這種情況。9/21 的 daily 分區寫入時間是 9/22 10:55:40，符合這個說法，但我查不到是哪個入口觸發的，這一點仍屬推測。
(c) 持續時間：策略快取會一路維持休市時算出的結果，直到 9/29 16:30。StockInfoBar 則要等到 9/29 盤中 enriched_date == today、注入即時K棒之後才恢復。
(d) 新面向（原記錄未提）：fetch_date 對網路錯誤也回傳空表。模擬 ErrorClient 的結果是 daily.status=success，會重新發佈也會重算快取。所以 daily_pipeline.py:163-173 那道「daily 失敗就不重算策略快取」的 gate，碰到連線失敗實際上永遠不會觸發。交易日 16:30 若斷網，前一交易日的換手率會被清空，策略快取也會用舊價格重算。

【使用者情境】自選中 2454、3034、2379、6196、3545 這 5 檔個股受影響（4 檔 ETF 本來就沒有換手率）。會在 2026-09-25 16:30 發生，之後每逢平日休市都會重演：HiStock 列出 10/9（五）、10/26（一）。

【嚴重度】P2 恰當。這是衍生欄位的明確功能錯誤，日K原始資料不受影響，下一個交易日發佈後就會恢復，不是資料破壞，也不是口徑錯誤。high_turnover_surge 在休市期間會變成空結果，因為 NaN > 5 為 False。以 turnover_rate 加權評分的策略，在整欄 null 時 col_range 為 null，走 otherwise(0.5)，排名改由其他因子決定。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:180,198 — CronTrigger(day_of_week="mon-fri", hour=16/21 ...)，沒有休市判斷；:169 只有 result.daily.status == "failed" 才略過；:174-175 呼叫 refresh_enriched_cache、refresh_strategy_cache
      - backend/app/taiwan/realtime/calendar.py:56,65-79 — known_holidays 預設為空集合，非週末日一律回傳 None（未驗證），不會回傳 False；daily_update.py:163 用的就是這個預設日曆
      - backend/app/taiwan/providers/snapshot_provider.py:105-121,132-135 — fetch_date 吞掉 TWSE/TPEx 的例外，stat 非 OK 時回傳空表；backend/app/taiwan/daily_refresh.py:173-179 把空表記成 dates_skipped，不列入 failed_dates
      - backend/app/taiwan/daily_update.py:283-286（已是最新就是 success）、308-311（沒有 failed_dates 就是 success）、376-390（success/skipped 時執行 TaiwanShareCapitalStore().refresh() 與 refresh_recent()）
      - backend/app/taiwan/enriched_refresh.py:191-198（refresh_recent 重新發佈最近 5 個分區）、95（compute_enriched）；backend/app/indicators/pipeline.py:735 today=cn_today()；backend/app/share_capital.py:46-50、82-83、109-115（台股 trade_date != today 時為 null，且沒有歷史股本）
      - 模擬 AUDIT_SCRATCH/work/verify_turnover_holiday/sim.py（store、client 都是假物件，兩個會寫檔的模組以 sys.modules stub 取代）。輸出：is_trading_day(2026-09-25)=None；[9/25 16:30] target=2026-09-25 missing_range=(9/25,9/25)；[holiday-reply @ 9/25 16:30] daily.status=success dates_skipped=1 failed=[] republish_calls=['share_capital.refresh','enriched.refresh_recent'] pipeline_refreshes_strategy_cache=True；[network-error @ 9/25 16:30] 結果相同，也是 success
      - 計算 turnover.py（真實日K與 share_capital.parquet，只 patch pipeline.cn_today）：cn_today=2026-09-23 時，9/23 有換手率 5/9；cn_today=2026-09-24 為 0/9；cn_today=2026-09-25 為 0/9。2454 在 9/23 = 0.400354%，等於 6,425,491/1,604,952,613（官方 t187ap03_L 已發行普通股數）
      - 磁碟 parts.py：kline_daily_enriched 的 date=2026-09-22 分區 turnover_nonnull=0，date=2026-09-23 分區 1087（mtime 2026-09-23 22:07:41）；9/17、9/18、9/21 同樣為 0
      - log data/backend.log:45470-45477 — 2026-09-23 21:40 target=2026-09-23，daily=success（已是最新），仍然執行 'taiwan enriched refresh: 10576 rows across 2026-09-17..2026-09-23'
      - 前端 frontend/src/components/StockInfoBar.tsx:148 與 frontend\src\lib\turnoverRate.ts：台股的 float_shares 為 null，所以改用 latest.turnover_rate，受影響；EChartsCandlestick.tsx:911-912 只用 stockInfo.float_shares，而台股永遠為 None（backend\app\api\kline.py:195），休市前後都不會顯示換手
      - 策略：backend\app\strategy\builtin\high_turnover_surge.py:67-69 turnover_rate > min_turnover（null 時結果為空）；bullish_alignment.py:36、low_volatility_leader.py:46、pullback_to_support.py:56 turnover_rate 權重 0.3；backend\app\strategy\engine.py:1515-1519 整欄 null 時 col_range 為 null，走 otherwise(0.5)，換手率不再影響排序
      - 定義：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a 原文「週轉率是當日交易的總股數與上市的總股數之比」
      - 休市：https://histock.tw/holiday.aspx?id=TWSE 列出 2026/09/25（星期五）、2026/09/28（星期一）「孔子誕辰紀念日/ 教師節」休市，另有 2026/10/09（星期五）、2026/10/26（星期一）。HiStock 把 9/25 標成「端午節」，推測是該頁的標註錯誤，日期與 ctee 一致
      - 隔日補跑：data\taiwan\daily\date=2026-09-21\part.parquet 的 mtime 為 2026-09-22 10:55:40；backend.log.1 沒有 9/21 排程執行的紀錄；觸發入口不明（推測是手動或其他路徑，排程 misfire_grace_time=3600 不會跨日）
      - 既有記錄比對：docs/code-review-2026-09-22.md:646-657 的 N1 讓 StockInfoBar 改讀後端 turnover_rate，但沒有提到休市或牆鐘日期造成的 null 化，所以這條不是重複回報
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：排程週一至週五 16:30 與 21:40 都會執行。預設的 TaiwanTradingCalendar 沒有假日資料，休市的平日 is_trading_day 會回傳 None，於是目標日被設成休市日本身，daily 去抓該日。抓到空結果時計入 dates_skipped，daily.status 回報 success。daily 已是最新時（例如 9/23 21:40）也回報 success。兩種情況都會接著執行 TaiwanShareCapitalStore.refresh、refresh_recent（重新發佈最近 5 個分區）與策略快取重算。

apply_historical_float_shares 以牆鐘時間 cn_today() 判斷，台股凡是 date ≠ 今天的列，換手率都設為 null。實測：9/23 分區目前有 1,087 檔換手率有值，只要在 9/24 以後重新發佈就會全部變成 0 檔。

依現行設計，任一交易日的換手率在下一次發佈時都會被清空。這個缺陷的新增傷害是休市日照樣重新發佈，使最新交易日也失去換手率，所有分區全部為 null：2026-09-25（五）16:30 起，9/24 的全市場換手率被清空，9/25 21:40、9/28 兩班會再清空一次，每班還會對 TWSE 與 TPEx 多發抓取請求；策略快取也以 null 換手率重算。全市場 enriched 資料要到 9/29 16:30 的正式發佈才恢復；9/29 盤中只有自選個股可能由即時報價在記憶體補上當日換手（未實測）。

隔日才補跑的交易日（例如 9/21，第一次發佈在 9/22 16:30）從未有換手率。不過該日是和次一交易日同批發佈，最新分區仍有值，所以沒有使用者看得到的損失。
    - 理由：我用本地 parquet 加上純函式 compute_enriched 重算，finding 的數字完全重現。重算時只替換記憶體中的 app.indicators.pipeline.cn_today，沒有改任何 repo 檔案。

(1) 磁碟上 kline_daily_enriched/date=2026-09-23 有 1,087 檔的 turnover_rate 有值。模擬 today=9/23 時算出 1,087 檔，與磁碟逐檔差值上限 0.0。模擬 today=9/24 或 9/25 時變成 0 檔。9/17～9/22 各分區在三種模擬下都是 0，和磁碟相同。finding 說的「1,087 檔」「9/23 全部變 null」兩項都對得上。

(2) 休市日路徑也重現了。預設的 TaiwanTradingCalendar 沒有任何假日資料，is_trading_day(9/25) 回傳 None，所以 16:30 的目標日是 9/25，程式會去抓 9/25。抓到空結果時計入 dates_skipped，沒有 failed_dates，daily.status 因此是 success，接著執行股本刷新、refresh_recent 與策略快取重算，而發佈時的 today 是 9/25。9/25 的 21:40 班、9/28 的兩班也一樣。另外我從 backend.log 確認，9/23 21:40 那班在 daily 已是最新時沒有發出 MI_INDEX 請求，仍回報 daily=success 並重新發佈，所以「已是最新仍重新發佈」成立。

(3) 休市日期已由工商時報原文確認。

(4) finding 標為「推測」的 9/21 那一項，可以升為確認。compare2 所說 9/22 10:55 的寫入時間，指的是 taiwan/daily 日K分區，不是 enriched 分區。enriched 第一次涵蓋 9/21 的發佈在 9/22 16:30:09，當時 today=9/22，所以 9/21 從未有換手率。

需要修正的細節有三點：
- 休市日的機制不是 daily 已是最新，而是「行事曆不知道假日 → 去抓休市日 → 抓到空結果算成功」。結果一樣，但休市日每班還會多打 TWSE 與 TPEx 各一次請求。
- 依現行設計，任一交易日的換手率在下一次發佈時都會被清空，平常交易日也一樣。實際的新增傷害是休市期間連最新交易日也沒有換手率，所有分區全部為 null。9/21 的補跑因為和 9/22 同批發佈，沒有造成使用者看得到的損失。
- 全市場 enriched 與策略快取要到 9/29 16:30 的正式發佈才恢復；盤中即時報價只會在記憶體補自選個股的當日換手，這點我沒有實測。

使用者的 9 檔自選中，5 檔個股會受影響；4 檔 ETF 本來就是 null。嚴重度維持 P2。
    - 證據：
      - 本地重算（v2_repro.py → v2_repro.out，位於 AUDIT_SCRATCH/work/verify-turnover-clear/）：磁碟 9/23 有值 1087 檔；模擬 today=9/23 為 1087 檔，與磁碟逐檔差值上限 0.0；today=9/24 為 0；today=9/25 為 0。9/17、9/18、9/21、9/22 在三種 today 下都是 0。
      - 自選 9/23 換手率（官方 MI_INDEX 成交股數 ÷ t187ap03_L 已發行普通股 ×100 → 磁碟 → 模擬 today=9/24）：2454 為 0.4007→0.4004→null；3034 為 0.4668→0.4668→null；2379 為 0.9480→0.9480→null；6196 為 0.8051→0.8051→null；3545 為 0.1853→0.1853→null。0050、00878、00885、00893 一律 null（ETF 沒有股本資料）。
      - v1_disk.py 讀磁碟各分區（mtime／換手率有值筆數）：9/17、9/18、9/21、9/22 的 mtime 都是 2026-09-23 22:07:41，有值 0 筆；9/23 為 2026-09-23 22:07:41，1323 列，有值 1087 筆。
      - v3_holiday_path.py → v3_holiday_path.out（使用假 store 與假 adapter，不寫檔）：『9/25 Fri 16:30: is_trading_day=None target=2026-09-25 d_range=(9/25,9/25) daily.status=success -> enriched publish=True』；9/25 21:40 同上；9/28 16:30 的 d_range=(9/25,9/28)，dates_skipped=2，status=success；9/23 21:40 為『Daily store already current』，status=success。
      - backend/app/share_capital.py:39-51,58 與 82-83：沒有傳入歷史股本時，台股 date != today 的列 float_shares 設為 None；即使傳入歷史股本，台股也會被排除在外。
      - backend/app/indicators/pipeline.py:735 apply_historical_float_shares(df, historical_shares, today=cn_today())；backend/app/taiwan/enriched_refresh.py:95 呼叫 compute_enriched 時沒有傳 historical_shares；191-198 的 refresh_recent 重新發佈最近 5 個分區。
      - backend/app/taiwan/realtime/calendar.py:65-79：平日不在 known_holidays 就回傳 None；backend/app/taiwan/daily_update.py:108（只有 is False 才往前退）、163（預設行事曆沒有假日）、283-286（已最新時設為 success）、307-311（沒有 failed_dates 就是 success）、376-390（success 就刷新股本並 refresh_recent）；backend/app/taiwan/daily_refresh.py:173-180（抓到空結果計入 dates_skipped，不算失敗）；backend/app/taiwan/providers/snapshot_provider.py:103-120（抓取例外被吞掉，回傳空表）。
      - backend/app/jobs/daily_pipeline.py:169-174 在 daily 不是 failed 時執行 refresh_enriched_cache 與 refresh_strategy_cache；180 與 198 的 CronTrigger 為 day_of_week="mon-fri"，沒有休市判斷。
      - data/backend.log:45470-45478：2026-09-23 21:40 那班從開始到 enriched refresh 之間沒有 MI_INDEX 請求，記錄為『taiwan enriched refresh: 10576 rows across 2026-09-17..2026-09-23』與『daily=success』。
      - 9/21 補跑：data\taiwan\daily\date=2026-09-21\part.parquet 的 mtime 為 2026-09-22 10:55:40，對應 backend.log.1:53722 的 MI_INDEX?date=20260921 請求，由 enrichment_backfill 觸發。9/18 17:03 之後，第一次 enriched 發佈是 backend.log:2432『2026-09-22 16:30:09 ... across 2026-09-16..2026-09-22』，當時 today=9/22，所以 9/21 的換手率在發佈時就是 null。
      - https://www.ctee.com.tw/news/20260917700877-430201 原文：『因9月25日（周五）為中秋節暨9月28日（周一）為教師節，集中交易市場休市』『台股將從9月25日到28日，連同周六及周日，共將休市四天』。
      - 受影響策略：backend/app/strategy/builtin/high_turnover_surge.py:67-69（turnover_rate > min_turnover 過濾，預設開啟，門檻 5.0）與 44（評分權重）；bullish_alignment.py:36、low_volatility_leader.py:46、pullback_to_support.py:56（turnover_rate 權重各 0.3）；factor_rank_research.py:89 也用到 turnover_rate。
      - 範圍外附帶觀察（未深查）：9/23 官方與磁碟換手率在 1087 檔中有 20 檔差值 > 0.001，差值上限 0.224（6464.TWSE：本地成交量 2,300，官方 302,300）；差異來自成交量，分母相同。
      - 工作樹檢查：git status 與開始時相同，只有既有的 3 個修改；data 下的 enriched 9/23 分區與 share_capital.parquet mtime 都沒變；所有腳本與輸出只寫在 scratchpad\audit\work\verify-turnover-clear\。

### [P3 → P3] TDR 的分母與市值用外國原股總數，不是在台 TDR 單位數（確認成立）

- **主張**：官方欄位對 TDR 提供的是「TDR原股發行股數」；證券主檔把 TDR 標為 stock，所以換手率與市值都以原股總數計算
- **正確的定義或數值**：TDR 換手率以在台流通的 TDR 單位數為分母，市值以 TDR 單位數 × 價格計算，或不顯示
- **app 實際行為**：以外國公司全部股數計算，換手率幾乎為 0
- **對使用者的影響**：10 檔 TDR 的換手與市值失真；不影響自選股
- **修法建議**：TDR（代號 91 開頭或名稱含 -DR）不提供換手率與市值，或另找 TDR 流通單位數來源
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/taiwan/share_capital_store.py:115
  - backend/app/api/kline.py:176,191
- **線上證據**：
  - （無）
- **本地計算證據**：
  - twse_t187ap03_L：910322 康師傅-DR 的股數為 5,620,600,000；app 在 9/23 的 turnover_rate 為 0.000227%（量 12,747）；市值會顯示 23.05 × 5.62B ≈ 1,295.5 億
  - security_master.parquet：910322.TWSE 的 instrument_type = stock
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：官方 t187ap03_L 對 10 檔 TDR 提供的欄位是「TDR原股發行股數」，也就是外國公司的原股總數。security master 把這 10 檔都標為 stock，所以換手率分母與個股資訊列的市值都用原股總數。

910322 在 9/23 的數字：換手率 12,747/5,620,600,000 = 0.000227%，市值顯示 23.05 × 5.6206B = 1,295.55 億。以 2009 年發行規劃的 3.8 億 TDR 單位當上限，換手率至少被低估 14.79 倍。

這個市值既不是 TDR 市值（上限約 87.6 億），也不是公司市值。每單位 TDR 表彰 0.5 股，按比例換算的公司市值約 2,591 億。各檔 TDR 的表彰比例有 0.5、1、2 股三種，所以失真倍率各不相同。

現在在台流通的 TDR 單位數，本次查不到公開來源。
    - 理由：我從官方參考資料和本地 parquet 各自重算了一次。finding 列的每個數字都完全重現：官方欄位名稱、910322 的 5,620,600,000 股、9/23 量 12,747、turnover_rate 0.000227%、市值 1,295.55 億、security master 標成 stock、TDR 共 10 檔、自選股裡沒有 TDR。程式路徑 share_capital_store.py:115、kline.py:176,191 的行號與內容也都正確。

線上來源另外確認 TDR 的表彰比例不是 1:1。康師傅-DR 每 1 單位 TDR 表彰 0.5 股，2009 年預計發行 3.8 億單位，對應 1.9 億股原股。其他 TDR 的比例有 0.5、1、2 股三種。

所以分母 5.62B 至少是在台 TDR 單位數上限（3.8 億）的 14.79 倍。市值 1,295.55 億也兩頭不對：按比例換算的公司市值約 2,591.10 億（23.05/0.5×5.6206B），在台 TDR 市值的上限約 87.59 億（3.8 億×23.05）。

finding 的說法成立，只是低估了市值的失真：這個數字「既不是 TDR 市值，也不是公司市值」，而且各檔 TDR 的失真倍率會隨表彰比例不同。現在在台流通的 TDR 單位數查不到（Goodinfo 回 403，鉅亨頁面由前端動態產生），所以只能用 2009 年的發行規劃當上限，不是當下的流通數。

嚴重度維持 P3。依使用者情境，自選 9 檔沒有 TDR，監控規則 0 條。影響只限於打開 TDR 個股時預設顯示的市值（stock-info-fields.ts:24 visible:true），以及選股依 turnover_rate 排序時 TDR 會墊底。若嚴格照 CONTRIBUTING 的「明確功能錯誤」，可主張為 P2。
    - 證據：
      - 官方原始 JSON AUDIT_SCRATCH/reference/raw/twse_openapi_t187ap03_L.json：910322 的欄位原文為「已發行普通股數或TDR原股發行股數 = 5620600000」，產業別 91，面額「美金0.0050元」，出表日期 1150922
      - 官方資料裡產業別 91 或名稱含 -DR 的共 10 檔（9103、910322、9105、910861、9110、911608、911622、911868、912000、9136），全部在 data/taiwan/share_capital.parquet 裡，issued_shares 與官方值完全相同（910322 為 5620600000，report_date 2026-09-22）
      - data/taiwan/security_master.parquet：這 10 檔的 instrument_type 都是 stock，industry 為「未知產業（91）」
      - backend/app/taiwan/share_capital_store.py:115：TWSE 的 share_keys 為 ("已發行普通股數或TDR原股發行股數",)；:172-174 把 issued_shares 當成 float_shares 分母
      - backend/app/api/kline.py:176 is_stock = instrument_type == "stock"；:191 total_shares = issued_shares。frontend\src\components\StockInfoBar.tsx:146 marketCap = close * totalShares；frontend\src\lib\stock-info-fields.ts:24 的 market_cap 預設 visible: true
      - 本地重算（腳本 AUDIT_SCRATCH/work/verify-tdr/verify.py，輸出 out1.txt）：kline_daily_enriched\date=2026-09-23 的 910322 close=23.05、volume=12747、turnover_rate=0.00022679%。重算 12747×100/5620600000 = 0.000227%，與 app 一致；官方 MI_INDEX 9/23 的 volume_shares=12747、close=23.05 也吻合
      - 數字並列（out2.txt）：finding 的換手率 0.000227%＝本地重算 0.000227%；finding 的市值 ≈1,295.5 億＝本地重算 1,295.55 億。以 2009 年 3.8 億單位計的換手率為 0.003354%（14.79 倍）；以 0.5 股比例換算的公司市值為 2,591.10 億；TDR 市值上限為 87.59 億。同日 2454 的換手率 0.4004%，是 910322 的 1,765 倍
      - 線上：https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=e80ad7b6-4525-4add-a4e3-e535eaf715a4 原文「每1單位康師傅控股有限公司臺灣存託憑證表彰該公司普通股0.5股」
      - 線上：https://www.ctee.com.tw/news/20091103700075-439901 原文「本次TDR預計發行3億8千萬單位」「擬每兩單位TDR表彰一股普通股」「意即原股提發1億9千萬股」
      - 線上：https://tw.stock.yahoo.com/news/美德醫等10家上市臺灣存託憑證8-21收盤價彙總表-003100040.html 的 TDR 表彰數：美德醫療 1 股、康師傅 0.5 股、泰金寶 1 股、神州 0.5 股、越南控 2 股、明輝 0.5 股、泰聚亨 2 股、同方友友 0.5 股、晨訊科 2 股、巨騰 1 股
      - 線上：https://tw.stock.yahoo.com/quote/910322.TW/profile 顯示「已發行普通股數 5,620,600,000」，但「市值 -」（Yahoo 對這檔 TDR 不給市值）
      - 查不到的部分：在台流通的 TDR 單位數目前值。goodinfo.tw 回 HTTP 403；cnyes 的 dr.aspx 與 intro.aspx 由前端動態產生，抓不到數值
      - 使用者影響：自選 9 檔（2454、3034、2379、6196、3545、0050、00878、00885、00893）都不是 91 開頭，不受影響。repo 的 git status 與 session 開始時相同，本次沒有寫入 repo

### [P3 → P3] ETF 換手率與市值恆為空（自選 9 檔中有 4 檔 ETF），第三方有提供 ETF 週轉率（確認成立）

- **主張**：股本資料不含 ETF，而且各路徑都排除非 stock 標的，所以 0050、00878、00885、00893 的換手率一律顯示「—」；docs 把 ETF 單位數排除在「公司股本」之外，屬設計取捨
- **正確的定義或數值**：如果要與第三方一致，ETF 週轉率 = 成交單位數 ÷ 發行單位數
- **app 實際行為**：一律為「—」
- **對使用者的影響**：自選頁預設顯示的換手率欄，有 4/9 列永遠是「—」
- **修法建議**：交由使用者裁決：用 t187ap47_L 的發行單位數作為 ETF 換手率分母（只用當日值），市值維持不顯示
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/watchlist_enrichment.py:90
  - backend/app/services/quote_service.py:165-174
  - backend/app/api/kline.py:176
  - docs/taiwan-share-capital-phase-6h.md:104（ETF issued or beneficiary units as company share capital）
- **線上證據**：
  - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm：009828 週轉率 8.85%、00715L 為 10.08%
- **本地計算證據**：
  - compare4.out：009828 的 80,129,544 ÷ 905,045,000（t187ap47_L 發行單位數）= 8.854%，與第三方相符；00715L 為 9.99%，第三方 10.08%
  - enriched 9/23 的 0050、00878、00885、00893 的 turnover_rate 都是 null
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：finding 成立，只需修正兩點細節。

**換手率**
- 0050、00878、00885、00893 的換手率一律為 null，自選頁預設顯示的換手率欄因此顯示「—」。
- 有兩個原因同時成立：
  - watchlist_enrichment.py:90 和 quote_service.py:165-174 明確只處理 instrument_type 為 stock 的標的。
  - 分母來源 share_capital.parquet 本身不含任何 ETF，官方 t187ap03_L 也不含。
- enriched 管線沒有明確的 ETF 篩選，那裡的 null 來自分母缺值，不是篩選。
- 同一個 9/23 分區裡個股有值（例如 2454 為 0.4004%），可見 ETF 是被排除，不是受「只算當日」規則影響。

**市值**
- kline.py:176 只為個股提供 total_shares，所以 ETF 市值是 null。StockInfoBar 會直接隱藏這個欄位，不顯示「—」。

**第三方對照**
- 富邦週轉率排行（9/23）把 ETF 算在內：009828 為 8.85%，我重算 8.854%，吻合；00715L 為 10.08%，我重算 9.988%，差 0.09 個百分點。推測是發行單位數的日期不同，未查證。
- 自選的 4 檔 ETF 不在該排行頁。我用官方資料算出的 9/23 值為 0050 0.263%、00878 0.141%、00885 0.194%、00893 0.234%。
- 若要改成與第三方一致，應採用成交單位數除以 t187ap47_L 發行單位數。

docs 已把 ETF 單位數排除在公司股本之外，屬設計取捨，維持 P3，由使用者裁決。
    - 理由：我用本地 parquet、官方參考資料和第三方原始 HTML 重算，finding 的數值全部重現。

1. 本地資料。kline_daily_enriched 的 date=2026-09-23 分區中，0050、00878、00885、00893 的 turnover_rate 都是 null。同一分區的個股有值，例如 2454 是 0.400354，等於 6,425,491 ÷ 1,604,952,613 × 100。所以 ETF 是 null，原因是被排除，而不是「只算當日」的規則。9/22 分區全部是 null，這是當日限制造成的，和 ETF 無關。
2. 股本來源。本地 share_capital.parquet 有 1,988 筆，其中代號以 00 開頭的是 0 筆。官方 t187ap03_L 參考檔中以 00 開頭的也是 0 筆。security_master 則有 391 檔 ETF，自選的 4 檔 ETF 的 instrument_type 都是 etf。
3. 重算週轉率。用官方 MI_INDEX 9/23 的成交股數除以 t187ap47_L 的發行單位數（report_date 9/22）：
   - 009828：80,129,544 ÷ 905,045,000 = 8.8537%。finding 寫 8.854%，富邦寫 8.85%，一致。
   - 00715L：5,562,559 ÷ 55,691,793 = 9.9881%。finding 寫 9.99%，富邦寫 10.08%，差 0.09 個百分點。推測是富邦用了不同日期的發行單位數；槓桿型 ETF 的單位數每天變動，我沒有查證。
4. 第三方原始 HTML。我用 curl 抓富邦 ZG_BD 頁面並解析，資料日期是 09/23。原文有兩列：「20|009828中信台日韓PCB|9.85|+0.20|+2.07%|80,130|8.85%」和「18|00715L期街口布蘭特正2|60.70|-3.85|-5.96%|5,563|10.08%」。這證明第三方的週轉率排行把 ETF 算在內，公式是成交單位數除以發行單位數。
5. 自選的 4 檔 ETF 不在該排行頁（0050、00878、00885、00893 在 HTML 中都查無），所以沒有第三方數值可以對照。我自己算的 9/23 值：0050 為 0.2633%，00878 為 0.1407%，00885 為 0.1940%，00893 為 0.2337%。
6. 前端。watchlist-columns.ts:34 的換手率欄預設 visible: true，Watchlist.tsx:1804 在 null 時顯示「—」。所以 finding 說「自選頁預設欄位中 4/9 列永遠是『—』」成立。

有兩點敘述需要修正，但不影響判定：
- 「各路徑都排除非 stock 標的」不完全精確。明確寫出 stock 篩選的是 watchlist_enrichment.py:90、quote_service.py:165-174，以及 kline.py:176（它決定 total_shares，也就是市值）。enriched 管線本身沒有 ETF 篩選，那裡的 null 是因為分母來源 share_capital.parquet 沒有 ETF。
- 市值方面，StockInfoBar 在值為 null 時直接隱藏這個欄位，不會顯示「—」。

這屬於文件已記載的設計取捨（docs/taiwan-share-capital-phase-6h.md:104），我維持 P3。
    - 證據：
      - backend/app/taiwan/watchlist_enrichment.py:90：`if q is None or asset_type != "stock": return None`
      - backend/app/services/quote_service.py:165-174：_taiwan_stock_symbols 只保留 instrument_type == "stock" 的標的；:195 用它篩選要補換手率的列
      - backend/app/api/kline.py:176：`is_stock = getattr(inst, "instrument_type", None) == "stock"`，:181 只有個股才讀 total_shares，所以 ETF 的市值是 null
      - backend/app/taiwan/enriched_refresh.py:90：分母來自 share_capital.as_instruments()，本身沒有 ETF 篩選
      - docs/taiwan-share-capital-phase-6h.md:104：「ETF issued or beneficiary units as company share capital」列在 Rejected inference
      - frontend/src/lib/watchlist-columns.ts:34：builtin:turnover 換手率 visible: true；frontend/src/pages/Watchlist.tsx:1804：null 時顯示 '—'
      - frontend/src/components/StockInfoBar.tsx:146,157：marketCap = totalShares ? close*totalShares : null；值為 null 時略過不顯示
      - 本地計算指令：`cd backend && PYTHONIOENCODING=utf-8 uv run python ...\scratchpad\audit\work\verify-etfturn\check.py`，輸出在 check.out
      - check.out：enriched date=2026-09-23 中 0050/00878/00885/00893 的 turnover_rate 為 null；2454=0.400354、2379=0.948032、3034=0.466788、3545=0.185321、6196=0.805123；台股 1,323 列中 1,087 列有值
      - check.out：share_capital.parquet 有 1,988 列，以 '00' 開頭的有 0 列；security_master 中 instrument_type 為 etf 的有 391 檔
      - 官方參考 twse_t187ap03_L_share_capital.csv：以 00 開頭的代號 0 筆；twse_t187ap47_L_etf_meta.csv（report_date 2026-09-22）：0050=22,051,000,000、00878=18,787,290,000、00885=591,238,000、00893=355,884,000、009828=905,045,000、00715L=55,691,793
      - 重算結果與 finding 並列：009828 為 8.853653%（finding 8.854%，富邦 8.85%）；00715L 為 9.988113%（finding 9.99%，富邦 10.08%）；自選 ETF 為 0050 0.263295%、00878 0.140720%、00885 0.194013%、00893 0.233720%（富邦頁面無這 4 檔可對照）
      - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm 原始 HTML（存於 verify-etfturn\zg_bd.html，解析結果在 zg_bd.txt）：「日期：09/23」、「20|009828中信台日韓PCB|9.85|+0.20|+2.07%|80,130|8.85%」、「18|00715L期街口布蘭特正2|60.70|-3.85|-5.96%|5,563|10.08%」
      - git status 驗證前後相同，只有 3 個原本就存在的修改檔，本次驗證沒有寫入 repo

### [P3 → P3] fmtBigNum 以「萬億」表示 10^12，台灣慣用「兆」（確認成立）

- **主張**：format.ts 在 ≥1e12 時輸出「萬億」（中國大陸用法）
- **正確的定義或數值**：64.83兆、8.32兆
- **app 實際行為**：64.83萬億、8.32萬億
- **對使用者的影響**：純台股部署的顯示用語不符在地慣例（數值正確）
- **修法建議**：≥1e12 改為輸出「兆」
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/lib/format.ts:29
  - frontend/src/components/StockInfoBar.tsx:157
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/%E5%85%86：「在台灣、日本和韓國，『兆』這個數字單位至今依然常用，表示萬億」；中國大陸稱 10^12 為「萬億」
- **本地計算證據**：
  - 2330 市值 2500 × 25,932,370,067 = 64,830,925,167,500，會顯示「64.83萬億」；2454 會顯示「8.32萬億」
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：原敘述正確：format.ts:29 在數值 ≥1e12 時輸出「萬億」。台股個股資訊條的市值（StockInfoBar.tsx:157，預設顯示）因此顯示為 2330「64.83萬億」、2454「8.32萬億」，台灣慣例應為「64.83兆」「8.32兆」（鉅亨網 2026-09-23 報導寫的就是 64.83 兆元）。數值本身正確，只有單位用語不符在地慣例。自選股中受影響的只有 2454。同一函式也用在 Dashboard 成交額等處，本地 9/23 全市場成交額未達 1e12，所以當天沒有觸發。
    - 理由：這條 finding 成立，嚴重度 P3 合理。

**程式碼**：format.ts:29 寫的是 `if (v >= 1_000_000_000_000) return ...萬億`。StockInfoBar.tsx:146 用 `close * totalShares` 算出市值，第 157 行交給 fmtBigNum 格式化。stock-info-fields.ts:24 把「市值」欄設為 `visible: true`，所以預設就會顯示。

**台股市值的資料來源**：kline.py:163-195 的 `_get_taiwan_stock_info` 對 instrument_type 為 stock 的標的，從 share_capital.parquet 取 issued_shares 作為 total_shares。本地 security_master 顯示 2330、2454 等 6 檔都是 stock、is_supported 為 true。

**數據重現**：
- 市值：本地 parquet 與官方參考資料的股本、9/23 收盤價逐筆相同。2330 為 2500 × 25,932,370,067 = 64,830,925,167,500；2454 為 5185 × 1,604,952,613 = 8,321,679,298,405。
- 格式化輸出：直接用 Node 24 import repo 裡真正的 format.ts 執行 fmtBigNum，得到「64.83萬億」與「8.32萬億」，與 finding 的 actual 一字不差。自選股中其餘 4 檔股票市值都低於 1e12，顯示為「億」，不受影響。
- 自選股影響範圍：只有 2454 落在這條分支（推測 2330 是稽核舉例用，不在自選清單內）。

**在地用語**：
- 維基百科原文：「在台灣、日本和韓國，『兆』這個數字單位至今依然常用，表示萬億」。
- 鉅亨網 2026-09-23 報導同一天、同一數值寫成「市值衝高至 64.83 兆元」，與本地計算吻合，證明台灣財經媒體的在地寫法是「兆」。

**影響**：數值正確，只有單位用語不符在地慣例，屬 P3（局部體驗）。

**補充面向（不改變判定）**：
- Dashboard.tsx:285 的「成交額」也用 fmtBigNum。本地 9/23 加總 887,250,849,184 → 「8872.51億」，當天沒有觸發萬億分支。不過本地 9/23 日K只有 1,330 列上市資料、上櫃 0 列。若日後上櫃資料也計入，官方上市總計 894,650,683,140 加上櫃 251,793,647,150 約 1.146e12，會顯示「1.15萬億」（推測，需上櫃資料實際入庫）。
- 邊界怪象：999,999,999,999 會輸出「10000.00億」（toFixed 進位後跨過單位邊界），不在原 finding 範圍。

**唯讀確認**：只寫入 scratchpad。git status 仍是原本那 3 個既有的改動，沒有新增。
    - 證據：
      - frontend/src/lib/format.ts:29：`if (v >= 1_000_000_000_000) return `${(v / 1_000_000_000_000).toFixed(2)}萬億``
      - frontend/src/components/StockInfoBar.tsx:146 `const marketCap = totalShares ? close * totalShares : null`；:157 `case 'market_cap': return marketCap != null ? fmtBigNum(marketCap) : null`
      - frontend/src/lib/stock-info-fields.ts:24：market_cap 預設 visible: true
      - backend/app/api/kline.py:180-195：台股 stock 的 total_shares 取自 TaiwanShareCapitalStore 的 issued_shares
      - 本地計算（uv run python ...\scratchpad\audit\work\verify-wanyi\calc.py）：2330 local_issued=25932370067（report_date 2026-09-22）、local_close=2500.0 → 64830925167500；2454 local_issued=1604952613、local_close=5185.0 → 8321679298405；兩檔都與官方 twse_t187ap03_L 股本及 STOCK_DAY 9/23 收盤逐筆相同
      - Node 24 直接 import repo 的 format.ts（...\verify-wanyi\fmt.mjs）的輸出：`2330 ... -> 64.83萬億`、`2454 ... -> 8.32萬億`、`2379 -> 3923.05億`、`3034 -> 3316.39億`、`6196 -> 1209.61億`、`3545 -> 114.10億`、`probe 1000000000000 -> 1.00萬億`、`probe 999999999999 -> 10000.00億`
      - 並列比較：finding 寫 64.83萬億／8.32萬億，本次重現結果是 64.83萬億／8.32萬億，完全一致；預期值 64.83兆／8.32兆
      - https://zh.wikipedia.org/zh-tw/%E5%85%86 原文：「而在台灣、日本和韓國，「兆」這個數字單位至今依然常用，表示萬億（1000000000000）」；「在中國大陸，「兆」通常作為國際單位制的接頭詞，用以表示百萬」
      - https://news.cnyes.com/news/id/6614281（台北2026-09-23 19:57）原文：「今日股價收在 2,500 元、大漲 40 元」「市值衝高至 64.83 兆元」，與本地計算的 64.83 相同
      - 補充：frontend/src/pages/Dashboard.tsx:285 的成交額也用 fmtBigNum；本地 9/23 market_totals 近似值 sum(amount)=887250849184 → 8872.51億（本地當日 TPEX 0 列、TWSE 1330 列）；官方上市總計 894,650,683,140 加上櫃 251,793,647,150 = 1,146,444,330,290 → fmtBigNum 為 1.15萬億（推測，需要上櫃資料入庫才會觸發）

### [P3 → P3] 自選頁台股列的振幅恆為「—」，成交額在 MIS 為來源時恆為「—」（確認成立）

- **主張**：watchlist_enrichment 刻意不提供 amplitude；amount 取即時報價，而 MIS 的 amount 固定是 None
- **正確的定義或數值**：自選頁振幅 = (高 − 低) ÷ 前一日收盤；成交額在收盤後取官方值
- **app 實際行為**：兩欄恆為「—」
- **對使用者的影響**：開啟這兩欄時看不到數值（預設隱藏）
- **修法建議**：以 q.high、q.low、q.prev_close 計算 amplitude；amount 在 MIS 缺值時改用 daily store 當日值
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/watchlist_enrichment.py:20-23,246
  - backend/app/taiwan/realtime/mis_provider.py:263
  - frontend/src/lib/watchlist-columns.ts:32,35（兩欄預設隱藏）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 即時報價已有 high、low、prev_close，振幅 (H − L) ÷ prev 可直接算出；enriched 同日也有官方 amount
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：自選頁台股列的振幅恆為「—」。watchlist_enrichment 刻意不輸出 amplitude，但 MIS 報價已有 h/l/y，算出的值與官方 (H−L)/前一日收盤完全一致（例如 9/23 的 2454 為 2.4131%）。成交額在來源是 MIS 或 Yahoo 時恆為「—」，因為兩個 provider 的 amount 都寫死為 None；只有第 4、5 層 fallback（官方收盤快照、本地日K）才有值。MIS 收盤後仍會回傳，所以使用者情境下這一欄實際上一直是空的。

修法有兩點要注意：
(1) 用 q.prev_close（MIS 的 y）當分母時，除息日會變成除以參考價，與「除以前一日收盤」的定義不同。例如 2379 在 9/08 是 5.8239% 對 5.6241%。
(2) 本地 daily store 的 amount 不一定等於官方值：9/16～9/23 有 16 列量與額偏低，例如 9/23 的 2454 少 25,090,650 元。直接改用它會繼承這個短少。

使用者目前存的欄位設定中，這兩欄都是隱藏的，所以只影響局部體驗，維持 P3。
    - 理由：我用參考資料加本地 parquet 重跑一次，finding 說的兩個「—」都能重現。

(1) 振幅。_TAIWAN_ROW_FIELDS 裡沒有 amplitude（watchlist_enrichment.py:40-44）。rows.append 也從未寫入 amplitude（:235-269），docstring 明寫這是刻意不做的（:20-23）。這個欄位要到 api/watchlist.py:458-461 用 setdefault 才補上 None，前端 primitives.tsx:65-66 遇到 null 就顯示「—」。Watchlist.tsx 沒有另外處理 amplitude（grep 查無），會直接走到 renderBuiltinDataCell（:1866）。

(2) 成交額。我把參考 MIS 快照餵給真正的 TwseMisRealtimeProvider.fetch_quotes（用 mock_response_json，沒有連網），5 檔的 amount 全是 None。原始 msgArray 裡也沒有任何成交額欄位。enrich_taiwan_watchlist_rows 換成 stub 服務（不做 IO）後輸出 amount=None，而且沒有 amplitude 這個鍵。Watchlist.tsx:1801 用 r.rt_amount ?? r.amount，但整個前端只讀 rt_amount、沒有地方寫入它，所以退回 r.amount，也就是 None。

資料本身其實足夠：MIS 的 h/l/y 算出的振幅，和官方 STOCK_DAY 的 (H−L)/前一日收盤在 5 檔都一致到小數第 4 位；本地日K 9/23 也有 amount。

需要補正或補充的地方：
(a) 不只 MIS，Yahoo provider 也是 amount=None（yahoo_provider.py:132）。只有第 4 層官方收盤快照和第 5 層本地日K會帶成交額（service.py:199、:264）。MIS 是第一順位來源，只要有成交價就會回傳，收盤後快照也一樣，所以使用者情境下這一欄實際上一直是「—」。
(b) finding 說「enriched 同日也有官方 amount」不精確。台股沒有 enriched store，有 amount 的是 daily store（data/taiwan/daily）。而且它不一定等於官方值：9/23 自選 9 檔裡 8 檔一致，2454 本地 33,363,492,540，官方 33,388,583,190，少了 25,090,650 元、5,000 股。8～9 月上市 10 檔共 380 列中，有 16 列的量與額都低於官方，全部落在 9/16～9/23。這是 finding 沒提到的另一個問題，會讓「改用 daily store 當日值」的修法繼承短少。短少原因推測是缺鉅額或盤後交易，我沒有查證。
(c) 修法寫的 q.prev_close（MIS 的 y）在除息日是參考價，不是前一日收盤，和 finding 自己的 expected（除以前一日收盤）不一致。以 2379 在 9/08 為例：除以前收 729 是 5.6241%，除以參考價 704 是 5.8239%。「y 在除息日等於參考價」是有依據的推論：TWT49U 的漲跌停 774/634 由 704 ±10% 取整而來，而 MIS 的 u/w 等於 y ±10%。但我沒有除息日當天的 MIS 快照可以直接證明。
(d) 使用者影響方面：preferences.json 已存的欄位設定裡，builtin:amplitude 和 builtin:amount 都是 visible=False，所以使用者現在看不到這兩欄，影響只在他自己開啟時才會出現。P3 合理。

振幅定義的線上核對：MBA 智庫寫明分母是「昨天收盤價」，和 A 股 pipeline.py:511-516 的實作相同。整個驗證過程工作樹沒有改變（git status 前後相同）。
    - 證據：
      - backend/app/taiwan/watchlist_enrichment.py:20-23 docstring 明列 amplitude 刻意不實作；:40-44 _TAIWAN_ROW_FIELDS 沒有 amplitude；:235-269 rows.append 沒有 amplitude 鍵；:246 amount=_sanitize_float(q.amount)
      - backend/app/taiwan/realtime/mis_provider.py:263 amount=None  # MIS does not provide cumulative amount
      - backend/app/taiwan/realtime/yahoo_provider.py:132 amount=None（第二順位來源同樣沒有成交額）；service.py:117-151 MIS 優先，:199／:264 只有官方快照與日K fallback 帶 amount
      - backend/app/api/watchlist.py:458-461 以 setdefault 補 None；frontend\src\components\stock-table\primitives.tsx:65-66 amplitude 為 null 時顯示「—」；frontend\src\pages\Watchlist.tsx:1801 fmtBigNum(r.rt_amount ?? r.amount)，rt_amount 全前端沒有寫入點（grep 只有 Watchlist.tsx:1801 與 stock-table.ts:70 兩處讀取）
      - 本地重現（指令：cd backend && uv run python ...\scratchpad\audit\work\verify-amp-amt\repro.py，輸出在 repro_out.txt）：原始 MIS msgArray 的鍵中沒有任何成交額欄位；真正的 MIS parser 解析後 2454／6196／0050／00878／6488 的 amount 全為 None；enrich_taiwan_watchlist_rows 的輸出沒有 amplitude 鍵、amount=None，high/low/prev_close 都有值
      - 振幅並列比較（9/23）：由 MIS 的 h/l/y 算出與官方 STOCK_DAY 的 (H−L)/前一日收盤：2454 2.4131% 對 2.4131%、6196 4.4860% 對 4.4860%、0050 0.7599% 對 0.7599%、00878 0.7952% 對 0.7952%、6488 8.5479% 對 8.5479%；自選其餘檔依官方資料：3034 3.4111%、2379 5.5629%、3545 3.0769%、00885 0.3286%、00893 1.1143%；自選頁實際顯示：全部是「—」
      - 本地 daily parquet data\taiwan\daily\date=2026-09-23 的 amount 和官方比對：9 檔中 8 檔相等；2454 本地 33,363,492,540 元，官方 33,388,583,190 元（少 25,090,650 元、5,000 股）。amount_diff.py：8～9 月上市 10 檔共 380 列，有 16 列量與額都低於官方，全部在 9/16～9/23（例如 2330 在 9/18 少 3,702,177 股、約 9.10e9 元）
      - 除息日分母差異（exdiv.py，資料來自 TWT49U 與 STOCK_DAY）：2379 在 9/08 H=721、L=680，除以前收 729 = 5.6241%，除以參考價 704 = 5.8239%；00878 在 8/18 為 1.3630% 對 1.4050%。TWT49U 中 2379 的漲跌停 774/634 = 704 ±10% 取整
      - 使用者設定 data\user_data\preferences.json 的 watchlist_columns：builtin:amplitude visible=False、builtin:amount visible=False（共 49 欄）；frontend\src\lib\watchlist-columns.ts:32、:35 預設也是 visible:false
      - 線上定義：https://wiki.mbalib.com/zh-tw/%E8%82%A1%E7%A5%A8%E6%8C%AF%E5%B9%85 原文「（當日最高點的價格－當日最低點的價格）/昨天收盤價×100%＝振幅」；repo 的 A 股實作 backend\app\indicators\pipeline.py:511-516 相同（除以 close.shift(1)）

### [P1 → P1] 16:30 排程早於鉅額交易收盤（17:00），抓到的日成交量與成交值偏少，而且永不重抓（部分成立）

- **主張**：每日 16:30 抓取 MI_INDEX 時，鉅額交易仍在進行，官方統計尚未定稿；21:40 那一班會略過已存在的日期，所以偏少的量值會永久留在 daily store 與 enriched
- **正確的定義或數值**：日K的 volume 與 amount 等於官方定稿的成交股數與成交金額
- **app 實際行為**：收盤後 16:30～17:03 抓取的分區中，有鉅額交易的標的量值偏少，而且之後不會修正
- **對使用者的影響**：成交量、成交額、當日換手率被低估（6464 在 9/23 為 0.0017%，應為 0.2257%）；vol_ma5、vol_ratio_5d、放量訊號與策略也會吃到錯的量（屬其他領域）；K 線資訊條的成交量與成交額也錯
- **修法建議**：把日線抓取移到 17:30 之後；或讓 21:40 那班對最近 1～2 個交易日 force 重抓並比對筆數與總量，不一致就覆寫
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/jobs/daily_pipeline.py:180（mon-fri 16:30）
  - backend/app/jobs/daily_pipeline.py:195（註解：已落盤的日期會被 refresh 略過）
  - backend/app/taiwan/daily_refresh.py:167-170（cur in available 就略過）
  - backend/app/taiwan/daily_update.py:283-286（Daily store already current 視為 success）
- **線上證據**：
  - https://www.tejwin.com/insight/%E9%89%85%E9%A1%8D%E4%BA%A4%E6%98%93%E6%98%AF%E4%BB%80%E9%BA%BC/：鉅額配對交易「08:00–08:30 及 09:00-17:00」，逐筆交易「09:00–17:00」
  - 參考資料 manifest：MI_INDEX 與 STOCK_DAY 的統計範圍含一般、零股、盤後定價、鉅額交易
- **本地計算證據**：
  - compare2.out 的分區修改時間：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30 這五天有缺口；9/21（9/22 10:55 補抓）與 8/03～9/15（9/16 11:21 回補）差異為 0
  - 9/23 上市 1,330 檔中 23 檔量值偏少，全部是 app < 官方，22 檔差異為 1,000 股的整數倍。合計少 12,235,374 股、成交值少 3,926,807,712 元。例如 6464 為 2,300 對 302,300 股，1227 為 246,331 對 1,117,331 股，1109 為 61,788 對 572,788 股
  - 自選股：2330 在 9/18 為 37,190,511 對 40,892,688 股（−9.1%），成交值 912.9 億對 1,003.9 億；6196 在 9/17 為 808,428 對 973,428 股（−17%）；2454 在 9/18 少 434,469 股（−4.2%）
  - log：2026-09-23 21:40:10 Scheduled Taiwan update (margin) finished ... daily=success，同時段沒有 MI_INDEX 請求
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：官方 MI_INDEX 的成交股數與成交金額包含鉅額交易。鉅額逐筆與配對交易都開放到 17:00，而且可以用 1 股為單位成交；MI_INDEX 要在 17:00 之後的某個時間才併入鉅額成交，9/18 在 17:03 抓取時仍然沒有，確切時間查不到。日線由 16:30 排程抓取，只要某日期已落盤，21:40 那班與之後各班都會以「Daily store already current」回報 success 並略過，平常沒有任何自動路徑會重抓。所以每天有鉅額交易的上市標的（9/23 為 1,330 檔中的 23 檔），日K的成交量與成交值會永久偏少，連帶影響 enriched 的換手率、vol_ma5、vol_ratio_5d 與放量訊號。使用者的自選股確實受影響（2454 五天皆偏少、最多 −4.19%；6196 在 9/17 −16.95%；3034、2379、3545 也偏少）。2330 不在自選股內，finding 把它列為自選股是錯的。「移到 17:30 之後」只是推測，比較穩健的修法是讓 21:40 那班對最近 1～2 個交易日強制重抓並比對總量。另外有一個原記錄沒提到的新面向：同一個「已落盤就略過」機制，也讓只抓到單一市場的分區永久殘缺。9/23 16:30 TPEx 連線失敗，只寫入上市列，daily 仍回報 success；9/16 與 9/23 的上櫃日K整天缺漏。
    - 理由：主張的核心成立，而且我在本地重算的數字與 finding 完全一致：當天下午抓取的上市日K少了鉅額交易的量值，之後也不會再修正。有三處需要修正。(1) 定義：證交所交易部的教材（證基會託管，與 TEJ 是不同來源）寫明鉅額逐筆交易 9:00-17:00、配對交易 8:00-8:30 與 9:00-17:00，以 1 股為交易單位。MI_INDEX 與 STOCK_DAY 原始 JSON 的附註都寫「本統計資訊含一般、零股、盤後定價、鉅額交易」，所以官方的成交股數與成交金額包含鉅額。(2) 程式碼：16:30 排程；run_update 在 d_max >= target 時，resolve_missing_date_range 回傳 None，直接回報「Daily store already current」並視為 success；refresh_dates 遇到已存在的日期也會略過。API 與前端都沒有提供 force 重抓，只有 CLI 的 --force 或重新 bootstrap 才會重抓。log 顯示 9/23 21:40 那一班沒有發出 MI_INDEX 請求，daily=success。(3) 資料：自選股與 2330 共 10 檔、380 列對照 STOCK_DAY。當天 16:19～17:03 抓取的分區（9/16、9/17、9/18、9/22、9/23）共有 16 列偏少，全部是 app < 官方。隔天或更晚才抓的分區（8/03～9/15、9/21）差異為 0，證明 parser 正確，偏差只來自抓取時間。9/23 全上市 1,330 檔中 23 檔偏少，其中 22 檔是 1,000 股的整數倍，合計少 12,235,374 股、3,926,807,712 元，與 finding 一致。Yahoo 轉載的 9/18 鉅額排行中，2330 前四筆合計 3,097,000 股、2454 有 383,000 股，差額與成交值的比例都與本地缺口吻合，佐證缺的就是鉅額交易。

需要修正的地方如下。(a) 機制講得太窄：9/18 在 17:03（鉅額收盤後）抓取仍然缺鉅額，所以不只是「鉅額還在進行」。MI_INDEX 要在 17:00 之後的某個時間才併入鉅額，確切時間查不到，因此「移到 17:30 之後」是推測，沒有驗證。比較穩健的修法是 21:40 那班對最近 1～2 個交易日強制重抓並比對。(b) finding 把 2330 列為「自選股」是錯的，使用者的自選股不含 2330。不過自選股確實受影響：2454 在五天都偏少（9/16 −3.75%、9/18 −4.19%）、6196 在 9/17 −16.95%，3034、2379、3545 在 9/18 也偏少；四檔 ETF 在比對期間沒有差異。(c) 原記錄沒提到的新面向：同一個「已落盤就略過」機制，也讓 9/23 16:30 的 TPEx 連線失敗（log：peer closed connection）只寫入上市 1,330 列，daily 仍回報 success，上櫃 9/23 整天永久缺漏；9/16 的分區同樣只有上市列。嚴重度：成交量、成交值與官方口徑不一致，每天都發生、靜默且永久，並連帶影響換手率、量比與放量訊號，所以維持 P1。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:180 CronTrigger mon-fri 16:30；:194-195 註解『已落盤的日期會被 refresh 略過, 不重複下載』；:196-204 21:40 那班 refresh_daily=True，但沒有 force
      - backend/app/taiwan/daily_update.py:282-286：d_range 為 None 且沒有 force 時，status='success'、note='Daily store already current'；:124-145 resolve_missing_date_range 在 earliest_available >= target_latest 時回傳 None；:39 DEFAULT_POST_CLOSE_HOUR=16
      - backend/app/taiwan/daily_refresh.py:150 沒有 force 時 available=store.available_dates()；:167-170 cur in available 就略過
      - backend/app/taiwan/providers/snapshot_provider.py:103-124 TWSE 或 TPEx 任一失敗只記 warning，仍回傳另一市場的資料並寫入分區
      - grep force backend/app/api/taiwan.py：沒有任何 force 參數；只有 daily_update.py 的 CLI 提供 --force（daily_update.py:445）
      - 官方附註（reference/raw/twse_mi_index_20260923.json，個股行情表 notes）：『本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。』；STOCK_DAY notes：『當日統計資訊含一般、零股、盤後定價、鉅額交易』
      - 證交所交易部教材《認識證券交易新制度》第 43 頁（https://webline.sfi.org.tw/download/lib_ftp/INServiceEdu/111-B-1-03.pdf）：『鉅額交易 逐筆交易…交易時間 9:00-17:00；配對交易 8:00-8:30；9:00-17:00』『交易單位 以1股為交易單位，但須符合最低數額標準』
      - Yahoo 9/18 證交所鉅額排行（https://tw.stock.yahoo.com/news/9-18%E8%AD%89%E4%BA%A4%E6%89%80%E9%89%85%E9%A1%8D%E4%BA%A4%E6%98%93%E6%97%A5%E6%88%90%E4%BA%A4%E9%87%91%E9%A1%8D%E5%89%8D20%E5%90%8D%E6%8E%92%E8%A1%8C-004300303.html）：2330 配對 1,602,000@2460、871,280@2455.77、423,720@2455.75、200,000@2450，合計 3,097,000 股；2454 383,000@4710。與本地缺口一致：2330 −3,702,177 股、−9.10e9 元；2454 −434,469 股、−2.05e9 元
      - 本地（scratchpad audit/work/verify-bigtrade/cmp.py）：分區修改時間 9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30，不一致列數依序為 2、4、6、2、2；9/21（9/22 10:55 抓）與 8/03～9/15（9/16 11:21 抓）共 330 列差異為 0
      - 本地（cmp923.py）9/23 全上市 1,330 檔：n_bad=23，app<official=23，mult1000=22，sum dv=-12,235,374 股、sum da=-3,926,807,712 元；6464 2,300 對 302,300、1227 246,331 對 1,117,331、1109 61,788 對 572,788
      - 自選股偏差：2454 9/16 6,391,865 對 6,640,865（−3.75%）、9/18 9,930,705 對 10,365,174（−4.19%）；6196 9/17 808,428 對 973,428（−16.95%）；3034 9/17 −33,000、9/18 −29,388；2379 9/18 −32,000；3545 9/18 −6,000；0050、00878、00885、00893 全部為 0
      - enriched（data/kline_daily_enriched/date=2026-09-23）：6464 volume=2300、turnover_rate=0.001717、vol_ratio_5d=0.173；0.001717×302300/2300≈0.2257，與 finding 一致
      - data/backend.log 45470-45480：2026-09-23 21:40:00 Starting run target=2026-09-23；21:40 只請求 t187ap03_L／mopsfin，沒有 MI_INDEX；21:40:10 finished daily=success
      - 新面向：backend.log:37258 2026-09-23 16:30:05 『TPEx snapshot fetch failed for 2026-09-23: peer closed connection…』；9/23 分區只有 TWSE 1,330 列，TPEX 0 列；9/16 分區同樣只有 TWSE 1,330 列；9/15、9/17、9/18、9/21、9/22 都有約 1,006～1,009 列 TPEX
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：日線全市場快照（MI_INDEX）在官方統計尚未把當日鉅額交易併入前就被抓取並落盤，之後所有路徑（包括 21:40 排程）都會略過已存在的日期，所以偏少的成交量與成交值會永久留在 daily store 與 enriched。實測的抓取時點包括 16:30 排程、17:03（排程延後觸發），以及 16:19 由非排程路徑觸發的抓取。9/18 在 17:03 抓的資料仍少了 2330 的 3,702,177 股，可見 17:00 剛過時資料也還沒定稿；「移到 17:30 之後」是否足夠沒有證據。app 以 16:00 作為可抓當日的門檻（daily_update.py:39-40,114-117），所以修法不能只改 cron 時間，應對最近 1～2 個交易日強制重抓，比對總量後覆寫，或延後這個門檻。上線後 5 個交易日中 5 天都有缺口：9/23 有 23 檔少量，合計少 12,235,374 股、3,926,807,712 元。自選股受影響的是 2454、3034、2379、6196、3545；finding 引用的 2330 不在自選清單。
    - 理由：我用官方參考資料和本地 parquet 各自重算，finding 列的數值全部一字不差重現。

1. 9/23：拿本地分區和 MI_INDEX 比對上市 1,330 檔，有 23 檔量值不符，全部是 app 小於官方，其中 22 檔差額是 1,000 股的整數倍（例外是 6669，差 229,374 股）。合計少 12,235,374 股、成交值少 3,926,807,712 元。6464、1227、1109 三檔的數字也和 finding 相同。價格欄位 0 筆不符。
2. STOCK_DAY 10 檔、8/03～9/23 共 380 列：有差異的只落在分區修改時間為 9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30 這五天，共 16 列。9/21（9/22 10:55 才抓）和 8/03～9/15（9/16 11:21 回補）都是 0 差異。2330 在 9/18、6196 在 9/17、2454 在 9/18 的數字和 finding 相同。
3. 永不重抓：程式碼和 log 都能證明。9/23 21:40 那一班沒有 MI_INDEX 請求，daily 直接回報 success，接著用錯的量重算了 9/17..9/23 的 enriched。enriched 裡 6464 在 9/23 的 turnover_rate 是 0.001717（%），用官方量計算應為 0.22571%。
4. 原因歸屬：我用 Yahoo 奇摩的證交所鉅額交易前 20 名交叉核對。9/17 前 20 名裡 2330 合計 575,000 股，app 少 597,000 股。9/18 前 20 名裡 2330 合計 3,097,000 股，app 少 3,702,177 股；2454 為 383,000 股，app 少 434,469 股。app 缺的量都不少於前 20 名的鉅額量，和「缺的是鉅額交易」的說法一致。

需要修正的三個細節：
(a) 2330 不在使用者的自選清單。自選股實際受影響的是 2454（5 天）、3034、2379、6196、3545；0050、00878、00885、00893 在這段期間都沒有差異。
(b) 「早於 17:00」不足以解釋全部。9/18 那次在 17:03:32 才抓（排程延後觸發），仍少了 2330 的 3,702,177 股，所以 17:00 剛過時官方數字也還沒定稿。「移到 17:30」是否足夠沒有證據。
(c) 9/17 那次 16:19 的抓取不是 16:30 排程觸發的，log 在 16:30 排程之前就出現 MI_INDEX 請求。resolve_target_latest_trading_date 把 16:00 當成可抓當日的門檻，所以其他路徑在 16:00 之後也會提早寫入並鎖死。修法要一併處理這個門檻，或者改成對最近 1～2 天比對後覆寫，只改 cron 時間不夠。

嚴重度：官方口徑包含鉅額交易，但落盤值系統性少算，而且永久保存，每個交易日都會發生（上線後 5 個交易日中 5 天都有），換手率、量能指標和放量訊號都會連帶出錯。依 CONTRIBUTING.md 的「錯誤金融口徑」維持 P1。

附帶觀察（不屬本 finding）：9/16 和 9/23 的分區完全沒有上櫃列（TPEX 0 筆），同一個「已存在就略過」機制會讓這個缺口也永久留存，可能已有其他 finding 涵蓋。

作業說明：我原本把腳本寫到 audit\work\verify-blocktrade\compare.py，後來發現這個資料夾已被先前的 agent 使用，裡面原有的 compare.py 已被我覆寫，無法復原。我已把我的腳本移到 verify-bt1630-repro\。該資料夾其他既有檔案 compare.out、mi_index_0923_diff.csv、enriched.py、enriched.out 都沒有動。repo 和 data\ 都沒有寫入。
    - 證據：
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-bt1630-repro/compare.py → compare_repro.out；enriched_check.py → enriched_check.out（指令：cd backend && uv run python <腳本>）
      - 9/23 MI_INDEX 對 app：joined 1330；不符 23；app<官方 23、app>官方 0；1,000 股整數倍 22；sum dvol=-12,235,374；sum damt=-3,926,807,712（與 finding 相同）
      - 6464：app 2,300 對官方 302,300；1227：246,331 對 1,117,331；1109：61,788 對 572,788；6669：差 -229,374（唯一不是 1,000 倍數的一檔）；價格不符 0 筆
      - STOCK_DAY 380 列：不符 16 列，只出現在 9/16(16:30:22)、9/17(16:19:15)、9/18(17:03:37)、9/22(16:30:03)、9/23(16:30:05)；9/21(9/22 10:55:40) 與 8/03～9/15(9/16 11:21) 全部 0 差異
      - 2330 9/18：37,190,511 對 40,892,688（-9.05%），成交值 91,291 百萬對 100,394,074,139 元；6196 9/17：808,428 對 973,428（-16.95%）；2454 9/18：-434,469（-4.19%）
      - enriched（data\kline_daily_enriched\date=2026-09-23）：6464 volume=2300、turnover_rate=0.001717；用官方量 302,300／已發行 133,932,793 股計算為 0.22571%
      - backend/app/taiwan/daily_update.py:283-286：d_range 為空時 status=success、note='Daily store already current'；daily_update.py:136-137：earliest_available>=target 時回傳 None
      - backend/app/taiwan/daily_refresh.py:167-170：cur in available 就略過（force=False 時）；repo 內 run_update 只在 daily_pipeline.py:149 與 CLI 被呼叫，排程不帶 force
      - backend/app/taiwan/daily_update.py:39-40,114-117：當日可抓門檻是 16:00（DEFAULT_POST_CLOSE_HOUR=16）
      - data\backend.log:45470-45478：2026-09-23 21:40:00 開始 → 21:40:09 'enriched refresh: 10576 rows across 2026-09-17..2026-09-23' → daily=success，中間沒有 MI_INDEX 請求
      - data\backend.log.1:48885-48890：9/18 的 16:30 排程在 17:03:32 才執行，MI_INDEX 請求時間 17:03:32，抓到的量仍偏少
      - data\backend.log.3:64279：9/17 16:19:12 的 MI_INDEX 請求出現在 16:30 排程（16:30:00）之前，不是排程觸發
      - https://tw.stock.yahoo.com/news/9-17證交所鉅額交易日成交金額前20名排行-004300428.html：2330 配對交易 149,000+92,000+80,640+80,565+75,000+50,435+47,360=575,000 股（app 同日少 597,000 股）
      - https://tw.stock.yahoo.com/news/9-18證交所鉅額交易日成交金額前20名排行-004300303.html：2330 共 1,602,000+871,280+423,720+200,000=3,097,000 股（app 少 3,702,177）；2454 共 383,000 股、1,803,930,000 元（app 少 434,469 股、2,046.3 百萬元）
      - https://www.master.tw/WMASHELP/DOWNLOAD_TIME.htm：鉅額交易逐筆「9：00至17：00」，配對「8：00至8：30。9：00至17：00」
      - 附帶：data\taiwan\daily 的 date=2026-09-16 與 date=2026-09-23 分區只有 TWSE 1330 列、TPEX 0 列（其他日約 1006～1009 列）

### [P2 → P2] 自選頁與盤中、收盤後當晚的換手率分子用 MIS 盤中整股量，與官方及第三方的總成交股數口徑不一致（部分成立）

- **主張**：_taiwan_turnover_rate 與 _fill_taiwan_turnover_rate 使用即時報價的 volume；MIS 的 v 是盤中整股張數 × 1,000，不含零股、盤後定價、鉅額。收盤後自選頁仍用這個值，所以與 K 線資訊條（MI_INDEX 量）顯示不同的換手率
- **正確的定義或數值**：收盤後的換手率以官方全日成交股數計算；盤中值若只能用整股量，應標示口徑
- **app 實際行為**：自選頁（預設顯示換手率）一整晚都顯示整股口徑，比官方低 6～8%；推測收盤後到午夜的 K 線資訊條也被即時K棒覆蓋成同一個值；午夜後才切換成官方值
- **對使用者的影響**：同一檔股票在自選頁與預覽視窗看到不同的換手率；高價股（例如 2454）零股比重大，低估最多
- **修法建議**：收盤後（或 daily store 已有當日資料時）改用 TaiwanDailyStore 的當日 volume 計算自選頁與即時K棒的換手率；盤中值加上「盤中整股」口徑標示
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/watchlist_enrichment.py:94-97
  - backend/app/taiwan/realtime/mis_provider.py:165-167（v 張 × 1000）
  - backend/app/services/quote_service.py:1688（即時 enriched 用同一個量補換手率）
  - backend/app/api/kline.py:533,571,582,590（當日 enriched 日期 = 今天時，即時列覆蓋官方的 volume 與 turnover_rate）
- **線上證據**：
  - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a：週轉率 = 「當日交易的總股數與上市的總股數之比」
  - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm：3094 成交 27,140 張，官方總成交股數 27,139,876（含零股）
  - https://histock.tw/stock/2454：成交量 5,902；https://tw.stock.yahoo.com/quote/2454.TW：成交量 5,909 張（整股口徑）
- **本地計算證據**：
  - mis_snapshot.csv：2454 的 v 為 5,902 張、6196 為 1,657 張；官方 MI_INDEX 為 6,430,491 股與 1,773,924 股
  - 依 app 公式計算自選頁的值：2454 為 5,902,000 ÷ 1,604,952,613 = 0.3677%，官方口徑 0.4007%（−8.2%），enriched 為 0.4004%；6196 為 0.7521%，官方 0.8051%（−6.6%）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：_taiwan_turnover_rate（watchlist_enrichment.py:81-97）以 MIS 報價的 volume（v 張 × 1000，只含盤中整股，不含零股、盤後定價、鉅額）當分子，並且只檢查 trade_date == 今天，不看 is_realtime。因此交易日盤中與收盤後到午夜前，自選頁（預設顯示換手率）都顯示整股口徑的換手率。這比官方全部成交股數口徑、富邦與 MoneyDJ 換手率排行的口徑偏低：9/23 的 2454 為 0.3677%，官方口徑 0.4007%，差 −8.2%；6196 差 −6.6%。零股比重越高的高價股，偏差越大。午夜後自選頁的換手率會變成 null（「—」），而不是切換成官方值。收盤後 K 線資訊條是否也被覆蓋成同一個值未獲證實：收盤後 MIS 報價的 is_realtime=False，會被 hybrid_provider.py:178 丟棄。修法：收盤後改以 daily store 的當日成交量或官方全日成交股數計算；盤中值標示「盤中整股」口徑。
    - 理由：核心主張成立：自選頁台股換手率的分子是 MIS 的 v（整股張數）× 1000。這個量不含零股、盤後定價與鉅額交易；官方成交股數與第三方換手率排行的分子則是全部成交股數。收盤後到午夜前，自選頁會持續顯示偏低的值。我直接呼叫 app 的純函式重算，2454 偏低 8.22%、6196 偏低 6.59%，與 finding 相符。

程式碼路徑（以現況行號為準）：
- watchlist_enrichment.py:92 只檢查 q.trade_date == today，不看 is_realtime。
- :97 直接以 volume*100/issued_shares 計算。
- service.py:121 收盤後仍以 MIS 為第一來源。
- mis_provider.py:166-167 把 v 乘上 1000。
- 抓取時間為 01:53 的快照裡，d 仍是 20260923。所以 9/23 從 13:30 到午夜，trade_date 都等於今天，函式會回傳整股口徑的換手率。

口徑依據：
- 證交所 MI_INDEX 與 STOCK_DAY 原始附註寫明「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」。
- 與 finding 不同的來源 CMoney 的定義是「股票周轉率 = 股票交易的數量 ÷ 股票發行總數」。
- 我把富邦（MoneyDJ 資料）9/23 週轉率排行前 10 名逐一與 MI_INDEX 對帳。成交張數全部等於「官方總股數 ÷ 1000 後四捨五入」，例如 3094 為 27,139,876 → 27,140，只取整股的話不可能是 27,140。10 檔中有 8 檔的週轉率等於「官方總股數 ÷ 已發行股數」，誤差在 0.01 個百分點內。實務上的換手率排行確實以全部成交股數為分子。

需要修正的部分：
1. finding 說「午夜後才切換成官方值」，這對自選頁不正確。午夜後 trade_date（9/23）不等於今天（9/24），_taiwan_turnover_rate 會回傳 None，自選頁顯示「—」，不是官方值。模擬結果也確認如此。
2. finding 推測收盤後的 K 線資訊條也被覆蓋成同一個值，這點我無法證實。收盤後的市場狀態是 POST_CLOSE（calendar.py:118-121），MIS 報價的 is_realtime=False（mis_provider.py:238），hybrid_provider.py:178 會把它丟棄。所以晚上的即時 K 棒不會拿到 MIS 的收盤量；它帶的是最後一次盤中輪詢，或 enriched 快取刷新後的值，不一定等於自選頁的數字。
3. 「第三方」只能部分成立。Yahoo 的 2454 成交量 5,909 張、成交金額 306.82 億，也是整股口徑；官方成交金額是 333.89 億。MIS 的 fv=7 加上 v=5,902 剛好等於 5,909，推測 Yahoo 多算了盤後定價，而 MIS 的 v 不含盤後定價。不過 Yahoo 本身不顯示換手率。以全部成交股數為分子的，是富邦與 MoneyDJ 的換手率排行。
4. 盤中的零股交易也是 09:00～13:30 連續進行，所以盤中值同樣少算零股。這屬於即時來源的限制，fix 建議標示口徑是合理的。

另外發現一個次要的出入：本地 daily store 的 2454 9/23 成交量是 6,425,491 股，官方是 6,430,491 股，少 5,000 股，所以 enriched 算出 0.400354%，不是官方口徑的 0.4007%。這不屬於本 finding 的範圍，原因沒有查。

使用者情境：自選 5 檔個股（2454、3034、2379、6196、3545）都受影響，4 檔 ETF 的 asset_type 是 etf，換手率本來就是 null。換手率欄位預設顯示（watchlist-columns.ts:34 visible: true），卡片檢視固定顯示（Watchlist.tsx:590）。

嚴重度：分母（已發行股數）與公式正確，只有分子的涵蓋範圍比官方少。偏差約 6～8%，高價股較大。這個值只用於顯示，使用者沒有監控規則，也不影響交易。維持 P2 恰當；若依 CONTRIBUTING 嚴格認定為「錯誤金融口徑」可能偏向 P1，但實際影響不足以阻斷。
    - 證據：
      - backend/app/taiwan/watchlist_enrichment.py:92 只檢查 `q.trade_date != today` 就回傳 None，不看 is_realtime；:97 `return _sanitize_float(volume * 100.0 / issued_shares)`；:268 自選列的 turnover_rate 由這個函式產生
      - backend/app/taiwan/realtime/mis_provider.py:166-167 `v_lots = _safe_float(item.get("v"))`／`volume_shares = int(round(v_lots * 1000))`；:238 is_realtime 只在 OPEN 時為 True；:263 amount=None
      - backend/app/taiwan/realtime/service.py:121 收盤後仍先呼叫 mis_provider.fetch_quotes，結果直接放進 results（:136-138），不依 is_realtime 過濾
      - backend/app/taiwan/providers/hybrid_provider.py:178 `if q.last_price is None or not q.source_meta.is_realtime: continue`：收盤後的 MIS 報價不會進入即時 K 棒與 enriched 路徑，所以 finding 對 K 線資訊條的推測不成立或無法證實
      - backend/app/taiwan/realtime/calendar.py:118-121 13:30 後的狀態是 POST_CLOSE
      - frontend/src/lib/watchlist-columns.ts:34 換手率欄位 visible: true；frontend/src/pages/Watchlist.tsx:590 卡片檢視固定顯示換手率
      - 官方附註（reference/raw/twse_mi_index_20260923.json 與 twse_stock_day_2454_202609.json）：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - reference/raw/mis_snapshot.json（抓取時間 2026-09-24 01:53:26）：2454 的 d=20260923、v=5902、fv=7；6196 的 v=1657、fv=1。MI_INDEX 9/23：2454 為 6,430,491 股、6196 為 1,773,924 股；已發行股數 2454 為 1,604,952,613、6196 為 220,329,529（t187ap03_L）
      - 本地模擬（uv run python ...\work\verify_turnover_mis\sim.py，把 mis_provider.taipei_now 固定後，餵入快照呼叫 fetch_quotes 與 _taiwan_turnover_rate）：9/23 20:00 時 2454 的 app 值 0.3677%，官方口徑 0.4007%，差 −8.22%；6196 的 app 值 0.7521%，官方口徑 0.8051%，差 −6.59%。9/24 00:30 時兩檔 app 值都是 None（顯示「—」，不是官方值）
      - https://www.cmoney.tw/learn/course/cmoney/topic/128：「股票周轉率 = （一段時間內）股票交易的數量 ÷ 股票發行總數」（與 finding 不同的定義來源）
      - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm（9/23 排行）與 MI_INDEX 對帳（...\work\verify_turnover_mis\fubon_check.py）：前 10 名的成交張數都等於 round(官方總股數/1000)，例如 3094 為 27,139,876 → 27,140、4956 為 28,884,688 → 28,885、6861 為 5,900,806 → 5,901；3094 週轉率 32.65%，總股數算出 32.6526%；1709 為 17.77%，總股數算出 17.7725%；8150 為 16.50%，總股數算出 16.4962%
      - https://tw.stock.yahoo.com/quote/2454.TW：成交量 5,909 張、成交金額 306.82 億元（官方 33,388,583,190 元），也是整股口徑；5,909 = MIS v 5,902 + fv 7，推測 fv 是盤後定價量
      - 本地 data\taiwan\daily\date=2026-09-23：2454 volume 6,425,491（比官方少 5,000 股）；data\kline_daily_enriched\date=2026-09-23：2454 turnover_rate 0.400354、6196 為 0.805123（附帶發現，不屬本 finding）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：自選頁台股的換手率（watchlist_enrichment.py:92-97）只要報價日期等於今天，就用 MIS 的 v（盤中整股張數 × 1000，不含零股、盤後定價、鉅額）當分子。MIS 收盤後仍回傳當日最終的 v，所以從收盤到當天午夜，自選頁都顯示整股口徑的換手率。重算結果：2454 為 0.3677%，官方全日口徑 0.4007%（−8.2%）；6196 為 0.7521% 對 0.8051%（−6.6%）。這兩檔是參考資料裡僅有的 MIS 樣本。

K 線資訊條不是整晚都被同一個值覆蓋：
- 16:30 前顯示盤中最後一輪即時 enriched 的值，是收盤集合競價前的量，也跟自選頁不同；
- 16:30 排程重載 enriched 後改成官方口徑，約 0.40%。

因此約 16:30 到 24:00 之間，自選頁（約 0.37%）與預覽視窗（約 0.40%）確定會不一致。

午夜後自選頁的換手率會變成 null（「—」），不會切換成官方值；也就是說，自選頁從不顯示官方全日換手率。

第三方方面：報價頁（HiStock 5,902 張、Yahoo 5,909 張）也是整股口徑；週轉率排行（富邦）則用官方總成交股數。

建議修法：收盤後，或 TaiwanDailyStore 已有當日資料時，改用日線的當日 volume；午夜後也改用日線值。盤中值標示為「盤中整股」口徑。
    - 理由：主要的數值差異完全重現。我的腳本用官方參考資料與本地 parquet 重算後，2454 與 6196 的每個數字都跟 finding 一致，到小數第 4 位。自選頁程式路徑也確認了：TaiwanRealtimeService 不論開盤或收盤都先打 MIS（service.py:116-121）。收盤後 MIS 仍回傳當日 d=20260923 與最終 v，參考資料是台北 9/24 01:53 抓的快照，v=5902。_taiwan_turnover_rate 只檢查 trade_date == today，不檢查 is_realtime（watchlist_enrichment.py:92-97），所以 9/23 收盤後到午夜，自選頁一直用 MIS 的整股量 × 1000 計算換手率。官方 MI_INDEX 與 STOCK_DAY 的附註寫明成交股數「含一般、零股、盤後定價、鉅額交易」，因此兩者的口徑確實不同。

下列部分不正確或需要修正：
(1)「推測收盤後到午夜 K 線資訊條也被即時 K 棒覆蓋成同一個值，午夜後才切換成官方值」不成立。即時 enriched 只收 is_realtime 的報價：hybrid_provider.py:178-183，以及 quote_service.py:1056-1070 的 _records_datable_today。收盤後的最終同步不會寫入即時 enriched；log 顯示 13:29:13 之後就沒有任何「enriched 增量」。16:30 的排程先跑 run_update，接著呼叫 refresh_enriched_cache（daily_pipeline.py:51-72、174），從磁碟重載官方 enriched。所以 K 線資訊條在 16:30 前是盤中最後一輪輪詢的值，跟自選頁不同；16:30 後就是官方口徑。兩邊出現「0.37% 對 0.40%」的時間窗是約 16:30 到 24:00。
(2) 午夜後自選頁不會切到官方值，而是變成 null（顯示「—」）：MIS 仍回傳 9/23，但 today 已是 9/24（watchlist_enrichment.py:92-93）。也就是說，自選頁從來不會顯示官方全日口徑的換手率。
(3)「與第三方的總成交股數口徑不一致」只對一半：HiStock 顯示 5,902 張，Yahoo 總量 5,909 張，這兩站報價頁的成交量是整股張數，跟 MIS 同口徑，而且都沒有週轉率欄位。富邦的週轉率排行則用官方總股數，我重算 3094：27,139,876 ÷ 83,117,089 = 32.65%，與該站一致。
(4)「低 6～8%」只有 2454、6196 兩個樣本；3034、2379、3545 在參考資料裡沒有 MIS 快照，無法重算。
(5) enriched 0.4004% 不等於官方 0.4007%，原因是本地 TaiwanDailyStore 的 2454 量是 6,425,491，比 MI_INDEX 少 5,000 股。這是另一個問題，不屬於本 finding。

嚴重度：只影響顯示用的換手率。使用者有 0 條監控規則，這個值不參與交易判斷。差距 6～8%，以兩位小數顯示也看得出來，屬於明確的功能與口徑落差，維持 P2。
    - 證據：
      - 重算腳本：AUDIT_SCRATCH/work/verify-turnover-mis/repro.py（指令：cd backend && uv run python <腳本>）
      - 2454 並列比較（我算的／finding）：MIS v=5,902 張 → 5,902,000 股／同；MI_INDEX 與 STOCK_DAY 皆為 6,430,491 股／同；已發行股數（本地 share_capital.parquet 與 openapi t187ap03_L 一致）1,604,952,613／同；app 公式 0.367737%／0.3677%；官方口徑 0.400665%／0.4007%；相對差 −8.2185%／−8.2%；本地 enriched turnover_rate 0.400354%／0.4004%
      - 6196 並列比較：MIS 1,657,000 股；官方 1,773,924 股；已發行 220,329,529；app 0.752055%／finding 0.7521%；官方 0.805123%／finding 0.8051%；相對差 −6.5913%／finding −6.6%；enriched 0.805123%
      - 前端以 toFixed(2) 顯示（frontend/src/pages/Watchlist.tsx:1804），實際會看到 2454 為 0.37% 對 0.40%、6196 為 0.75% 對 0.81%；換手率欄預設顯示（frontend/src/lib/watchlist-columns.ts:34，visible: true）
      - 官方口徑：reference/raw/twse_mi_index_20260923.json 個股表附註「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」；STOCK_DAY 附註「當日統計資訊含一般、零股、盤後定價、鉅額交易」
      - backend/app/taiwan/realtime/mis_provider.py:165-167（v 張 × 1000）；:208-213、238（收盤後 status=official_snapshot、is_realtime=False，但仍回傳報價）
      - backend/app/taiwan/realtime/service.py:116-121（MIS 永遠是第一順位，收盤後也一樣）；MIS 快照在 2026-09-24 01:53:26 抓取，仍回傳 d=20260923、t=13:30:00、v=5902（manifest query_time 與 raw/mis_snapshot.json）
      - backend/app/taiwan/watchlist_enrichment.py:92-97（只看 trade_date == today 就用 q.volume；不看 is_realtime；午夜後 trade_date 9/23 不等於 today 9/24，因此回傳 None）
      - backend/app/api/watchlist.py:448-451（自選頁台股列只來自 enrich_taiwan_watchlist_rows，沒有被 enriched 覆寫）
      - K 線反證：hybrid_provider.py:178-183 只收 is_realtime 的報價；quote_service.py:861-863、1056-1070 只把 status=realtime 的報價寫進當日 K 棒與 enriched；data/backend.log 顯示 9/23 最後一筆「enriched 增量」在 13:29:06，13:30:08 最終同步後沒有增量寫入
      - backend/app/jobs/daily_pipeline.py:51-72、169-175、178-192：16:30 run_update 後 refresh_enriched_cache 從磁碟重載官方 enriched；kline.py:533、571、582、590 的即時列覆寫從此改用官方值
      - 資訊列來源：kline.py:195 台股 float_shares=None，所以 StockInfoBar.tsx:148 經 frontend/src/lib/turnoverRate.ts 採用最後一列的 turnover_rate
      - 第三方：https://histock.tw/stock/2454 成交量「5,902張」，沒有週轉率欄；https://tw.stock.yahoo.com/quote/2454.TW「總量5,909」張，資料時間 2026/09/23 14:30，沒有週轉率欄；https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm 3094 成交 27,140 張、週轉率 32.65%，我重算 27,139,876÷83,117,089=32.6526%（4956 為 28.7672% 對 28.77%，1709 為 17.7725% 對 17.77%）
      - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a：「週轉率是當日交易的總股數與上市的總股數之比」
      - 附帶發現（不屬於本 finding，另案處理）：daily_vs_official.py 顯示本地 data/taiwan/daily/date=2026-09-23（寫入時間 16:30:05）有 23/1,330 檔上市股票的量少於 MI_INDEX，差額都是 1,000 的倍數，例如 2454 為 6,425,491 對 6,430,491，2330 為 21,978,873 對 22,817,873，1109 為 61,788 對 572,788；推測是 16:30 抓取後才成交的鉅額交易

### [P3 → P3] 股本刷新時，一個交易所失敗就整檔覆寫，另一交易所的股本暫時消失（部分成立）

- **主張**：refresh() 只用這次成功的交易所資料重建並覆寫檔案，與 docstring「One exchange failing does not discard the other」不符
- **正確的定義或數值**：失敗的交易所沿用上一版股本
- **app 實際行為**：16:30～21:40 之間，上櫃股本從檔案中消失，該次發佈的上櫃換手率全部為 null
- **對使用者的影響**：發生失敗的日子，上櫃換手率在 21:40 之前缺值（9/23 因上櫃日K同時缺漏而被掩蓋）
- **修法建議**：覆寫前先讀舊檔，保留失敗交易所的舊資料列，並記錄各列的 retrieved_at
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/share_capital_store.py:106-134（109 行是 docstring，130-134 行覆寫）
  - backend/app/taiwan/daily_update.py:383-390（先刷新股本，隨即發佈 enriched）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - backend.log：2026-09-23 16:30:20 WARNING taiwan share capital fetch failed for TPEX: ... incomplete chunked read；目前檔案的 retrieved_at 是 22:07（21:40 那班才補齊）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：TaiwanShareCapitalStore.refresh()（share_capital_store.py:113-134）在單一交易所失敗時，只用這次成功的資料重建檔案並整檔覆寫，不會保留舊檔中失敗交易所的資料列。docstring 其實明寫了部分刷新會取代舊檔，所以問題不在於和文件不符，而是設計沒考慮到舊檔已有另一交易所資料的情況，測試也沒有涵蓋。有 log 為證：2026-09-23 16:30:20 上櫃抓取失敗後，檔案被寫成只有上市 1095 檔，直到 21:40:02 才恢復為 1988 檔。16:30 之前檔案裡有沒有上櫃資料無法證明（9/22 19:31 上櫃也失敗過，之後沒有成功紀錄）。9/23 這次事件對換手率沒有可觀察的影響：當天上櫃日K整批缺漏，歷史列又依設計本來就是 null，重算後有沒有上櫃股本結果完全相同。不過如果上櫃股本失敗、上櫃日K卻正常（以 9/22 模擬），當天上櫃換手率會從 891 檔有值變成 0 檔有值；在目前的程式碼（8dac62a）下，上櫃個股資訊列的市值、即時 K 棒的換手率補值也會空白。缺值會持續到下一次成功的排程刷新（約 5 小時，或跨到下一個交易日）。使用者自選 9 檔全是上市，不受影響。
    - 理由：機制成立：refresh() 只用這次成功抓到的資料列建出 DataFrame，然後用 tmp.replace 覆寫整檔，沒有讀舊檔，所以前一版失敗交易所的資料列會整批消失。這點從程式碼、log、以及我在 scratch 重做的寫檔步驟三方面都得到確認。不過 finding 有三處說法不成立或證據不足：

(1)「與 docstring 不符」不成立。docstring 第 109-111 行寫的是「partial refresh still prices the listings it covers, and the previous file is only replaced once at least one exchange answered」，意思是部分刷新本來就會取代舊檔，而且只替這次涵蓋到的標的提供股本。實際行為和文件一致，只是沒考慮到舊檔裡原本就有另一個交易所的資料。對應的測試 test_one_exchange_failing_still_prices_the_other 也是從空目錄開始，沒有涵蓋這種情況。

(2)「上櫃股本從檔案中消失」只能部分確認。能確認的是：16:30:20 寫入的檔案只有 1095 檔（全是上市），一直到 21:40:02 才恢復成 1988 檔。但 16:30 之前檔案裡有沒有上櫃資料，無法證明。9/22 21:40 的排程沒有執行；9/22 17:30 功能上線之後，log 裡沒有任何一次上櫃股本成功刷新的紀錄；9/22 19:31:18 和 19:31:30 上櫃還失敗了兩次。所以上櫃股本可能本來就不在檔案裡，「消失」這個說法沒有證據支持。

(3)「該次發佈的上櫃換手率全部為 null」這句話本身是真的，但原因不是股本被覆寫。9/23 的 taiwan/daily 分區只有上市 1330 列，上櫃的 9/23 日K到現在都還缺。歷史日期的上櫃列則因為 fail-closed 設計（share_capital.py:46-48）本來就是 null。我重算的結果是：只有上市股本和上市加上櫃股本兩種情況，產出完全相同。所以 9/23 這次事件對換手率的實際影響是零，不只是「被掩蓋」而已。

反事實重現：如果上櫃股本失敗的那天，上櫃日K是正常的（以 9/22 為今日模擬），上櫃換手率非 null 的列數會從 891 掉到 0。這證明機制有實際傷害。

新面向：commit 8dac62a（9/24 01:30）之後，kline.py:185-191（資訊列的市值）、quote_service.py:199-203（即時 K 棒的換手率補值）、watchlist_enrichment.py:185-196 也會讀這個檔案，所以缺值期間上櫃個股的市值和換手率也會空白。但使用者自選的 9 檔全部是上市，不受影響；監控規則有 0 條。缺值的時間長度取決於下一次成功的排程：21:40 那班成功的話大約 5 小時；如果 21:40 也失敗，會一直缺到下一個交易日 16:30。

結果是 null 而不是錯誤的數值，而且是暫時性、局部的，所以維持 P3。
    - 證據：
      - backend/app/taiwan/share_capital_store.py:113-134：rows 只收集這次成功的交易所；130-134 用 DataFrame(rows) 加 tmp.replace(self.path) 整檔覆寫，沒有讀舊檔
      - backend/app/taiwan/share_capital_store.py:109-111 docstring 原文：『a partial refresh still prices the listings it covers, and the previous file is only replaced once at least one exchange answered』。它明寫部分刷新會取代舊檔，與行為一致
      - git show 2aa655a:backend/app/taiwan/share_capital_store.py 的 refresh() 與目前版本相同；8dac62a 只改了 load() 的快取，所以 9/23 16:30 執行的就是這段程式碼
      - data/backend.log:37260-37263：16:30:14 TWSE returned 1095 rows → 16:30:20,521 TPEX incomplete chunked read → 16:30:20,535『refreshed: 1095 symbols』
      - backend.log:45472-45475：21:40:02 TWSE 1095 + TPEX 893 → 『refreshed: 1988 symbols』
      - backend.log:31013-31014：9/22 19:31:18 與 19:31:30 上櫃股本抓取失敗兩次；backend.log 只有 9/22 16:30、9/23 16:30、9/23 21:40 三次 TaiwanDailyUpdateService，9/22 21:40 沒有執行。因此 9/23 16:30 之前的檔案內容無法確認
      - 目前檔案（mtime 2026-09-23 22:07:35）：TWSE 1095 列、retrieved_at 22:07:34.66；TPEX 893 列、retrieved_at 22:07:35.94（inspect_file.py 輸出）。finding 說『22:07』相符；backend.log:45744 另記錄 22:07:01 上櫃失敗一次，寫檔來源不明（推測是測試或手動腳本）
      - inspect_partitions.py：taiwan/daily/date=2026-09-23 的 mtime 為 16:30:05，1330 列，全是 TWSE、沒有 TPEX；kline_daily_enriched 9/17～9/23 共 10576 列（與 16:30 和 21:40 兩次 log 的 10576 一致）；9/23 分區只有 TWSE 1323 列，turnover 非 null 1087；9/17～9/22 的 TPEX turnover 非 null 全部是 0
      - repro.py [1]：在 scratch 重做 refresh() 130-134 的寫檔步驟，舊檔 {TWSE:1095, TPEX:893}，上櫃失敗後變成 {TWSE:1095}（上櫃 893 列全數消失）
      - repro.py [2A]（today=9/23，模擬 pipeline.py:732-744）：只有上市股本 → TWSE 1330 列中 1090 列非 null、TPEX 0 列；完整股本 → 結果完全相同。股本覆寫對 9/23 發佈沒有可觀察的影響
      - repro.py [2B] 反事實（today=9/22，上櫃日K 1009 列）：只有上市股本 → TPEX 非 null 0 列；完整股本 → TPEX 非 null 891 列
      - backend/tests/test_taiwan_share_capital_store.py:54-58：部分刷新的測試從空的 tmp_path 開始，沒有涵蓋『舊檔已有上櫃資料』的情況
      - 新增讀取點（commit 8dac62a，9/24 01:30）：backend/app/api/kline.py:185-191、backend/app/services/quote_service.py:199-203、backend/app/taiwan/watchlist_enrichment.py:185-196；使用者自選 9 檔全部是 .TWSE

### [P3 → P3] 換手率分母含尚未上市的私募股，與第三方口徑不同，私募比重高的公司被低估（部分成立）

- **主張**：已發行普通股數包含私募普通股（3 年內不得轉讓）。第三方 MoneyDJ 的分母經反推，恰好等於已發行股數 − 私募股
- **正確的定義或數值**：換手率分母反映可交易股數，或至少在欄位說明標示含私募股
- **app 實際行為**：私募比重高的公司，換手率最多低估一半以上
- **對使用者的影響**：換手率排序與門檻篩選時，這類股票排名偏後；不影響自選的 9 檔
- **修法建議**：改用 issued_common_shares − private_placement_shares（官方資料已有此欄），或在文件與欄位說明中明示口徑，由使用者裁決
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/taiwan/share_capital_store.py:115（取「已發行普通股數或TDR原股發行股數」）
- **線上證據**：
  - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm（9/23 週轉率排行：3055 為 25.39%、3042 為 10.46%、2434 為 17.69%）
- **本地計算證據**：
  - compare4.out：3055 的 app 值 21.789%，第三方反推分母 98.669M ≈ 114,974,918 − 16,300,000 = 98,674,918；3042 的 app 值 9.699%，反推 318.04M ≈ 342,992,971 − 25,000,000；2434 的 app 值 8.13%，第三方 17.69%（私募 27,489,119／37,000,000）
  - 有私募股的公司：上市 103 家、上櫃 168 家，其中 197 家私募比重 ≥10%；自選股中沒有
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：台股換手率的分母取官方「已發行普通股數」，其中包含私募普通股（share_capital_store.py:115-117、172-175）。全市場有 271 家公司有私募股：上市 103 家、上櫃 168 家，其中 197 家私募比重 ≥10%。這些公司的換手率明顯低於第三方：以 9/23 富邦 DJ 週轉率排行為例，3055 在 app 為 21.79%、第三方 25.39%；3042 為 9.70% 對 10.46%；2434 為 8.13% 對 17.69%；3229 為 3.11% 對 6.19%。

第三方分母並不總是等於「已發行 − 私募股數」。排行中 7 檔有私募股的公司，只有 4 檔符合（3055、3042、2033、6668）。另外 3 檔（2434、3229、2444）的第三方分母介於兩者之間；例如 2434 為 17.0M 股，Yahoo 市值反推也是 17.0M。推測官方「私募股數」欄包含已補辦上市的私募股。所以直接改用 issued − private_placement 會矯枉過正，例如 2434 會算成 31.6%。

比較穩妥的修法有兩種：
1. 取得交易所的「上市／上櫃股數」作為分母。
2. 在欄位說明明示「分母含私募股，私募比重高的公司會偏低」。

受影響的是私募比重高的公司，換手率排序與門檻篩選會偏低，低估幅度實測約一半（2434 的 app 值只有第三方的 46%）。本地資料目前只有上市標的，9/23 有 100 檔私募公司帶有換手率；自選 9 檔不受影響。
    - 理由：大部分數值重現成功，但 finding 的核心說法「第三方分母恰好等於已發行股數 − 私募股」只有部分成立，finding 自己舉的 2434 就是反例。

一、重現成功的部分
1. app 的分母包含私募股：官方 JSON 有兩個欄位，「私募股數」（TPEx 為 PrivateStock.shares）與「已發行普通股數或TDR原股發行股數」（TPEx 為 IssueShares）。store 只取後者。本地 share_capital.parquet 有 271 檔私募股數 > 0 的公司，271 檔的 issued_shares 全部等於官方已發行股數，等於「已發行 − 私募」的有 0 檔。
2. app 的換手率：本地 enriched 9/23 的值為 3055 = 21.789372、3042 = 9.699020、2434 = 8.127424，與 finding 所列一致。另外 100 檔私募公司的 enriched 值等於 volume×100/issued_shares，最大誤差 0.0。
3. 第三方數值：富邦 DJ 9/23 週轉率排行實際抓到 3055 為 25.39%、3042 為 10.46%、2434 為 17.69%，與 finding 一致。
4. 家數：有私募股的公司上市 103 家、上櫃 168 家，私募比重 ≥10% 的有 197 家（上市 67、上櫃 130），都與 finding 一致。自選中 5 檔個股的私募股數都是 0，4 檔 ETF 不在 t187ap03 名單內，所以自選不受影響。

二、不成立或需要修正的部分
1. 「分母恰好等於已發行 − 私募」只對部分公司成立。我拿排行前 50 名中有股本資料的 43 檔反推第三方分母，容許 ±0.005% 的四捨五入誤差：
   - 其中 7 檔有私募股。
   - 4 檔符合「已發行 − 私募」：3055、3042、2033、6668。
   - 3 檔不符合：2434 反推約 17.0M 股，已發行 − 私募為 9.51M；3229 反推約 32.25M，已發行 − 私募為 20.91M；2444 反推約 101.64M，已發行 − 私募為 85.43M。
   - 這 3 檔的第三方分母都落在「已發行 − 私募」與「已發行」之間。推測「私募股數」欄還包含已補辦公開發行並掛牌的私募股，所以它不等於「尚未上市股數」。
   - Yahoo 的市值可以佐證：2434 為 1,145.8 百萬元，除以收盤價 67.4 元剛好是 17,000,000 股；3055 為 20,080.159 百萬元，除以 203.5 元等於 98,674,000 股。
2. 修法建議會矯枉過正：直接改用「已發行 − 私募」，2434 的換手率會變成 31.618%，遠高於第三方的 17.69%。3229 會變成 9.544%，第三方為 6.19%；2444 會變成 6.020%，第三方為 5.06%。
3. 第三方本身也不是精確基準：前 50 名裡有 3 檔沒有私募股（3605、3016、2464），第三方分母仍然不等於官方已發行股數。這代表第三方的股數還有其他來源差異。
4. 「3 年內不得轉讓」不精確：證交法第 43 條之 8 規定，私募股交付滿 1 年後，在第 3 年內可以依持有期間與交易數量的限制轉讓，滿 3 年後才完全解除限制。
5. 影響範圍比 finding 小：本地 9/23 的 daily 與 enriched 資料只有 TWSE 標的（daily 1330 筆、enriched 1323 筆，沒有 TPEX）。所以使用者目前受影響的是上市私募公司，9/23 有換手率的是 100 檔；168 家上櫃公司不在本地資料中。

三、嚴重度
維持 P3。台股換手率的分母已在程式中註明為已發行普通股數、非流通股數（pipeline.py:122、share_capital_store.py:15-20），沒有特別指出私募股。這屬於口徑差異，不是資料破壞，而且不影響自選的 9 檔。
    - 證據：
      - backend/app/taiwan/share_capital_store.py:115：TWSE 只取「已發行普通股數或TDR原股發行股數」；:116-117 TPEx 取 IssueShares；:172-175 直接把它當作 float_shares。
      - backend/app/volume_units.py:28-30：turnover = volume_shares*100/float_shares。
      - backend/app/indicators/pipeline.py:122：欄位說明為「换手率(台股分母為已發行普通股數, 非流通股數)」，沒有提到私募股。
      - 官方 raw JSON（reference/raw/twse_openapi_t187ap03_L.json）：2434 的私募股數 27489119、已發行 37000000；3042 為 25000000／342992971；3055 為 16300000／114974918。TPEx raw 另有 PrivateStock.shares 欄位。
      - 腳本 verify-pp/repro.py，執行方式為 cd backend && uv run python，輸出在 repro.out：有私募的公司 TPEX 168、TWSE 103；比重 ≥10% 的共 197 家；自選個股的私募股數都是 0。
      - repro.out：3055 成交 25,052,313 股，app 值 21.7894%，已發行 − 私募算出 25.3887%，第三方 25.39%，相符。3042 成交 33,266,958 股，app 值 9.6990%，已發行 − 私募算出 10.4615%，第三方 10.46%，相符。2434 成交 3,007,147 股，app 值 8.1274%，已發行 − 私募算出 31.6180%，第三方 17.69%；第三方反推分母 16,999,135，容許範圍為 16,994,332～17,003,941 股，而已發行 − 私募為 9,510,881 股，不符。
      - 本地 enriched（data/kline_daily_enriched/date=2026-09-23）：2434 的 turnover_rate 為 8.127424、3042 為 9.699020、3055 為 21.789372。local_check.out：271 檔私募公司的本地 issued_shares 全部等於官方已發行股數，等於已發行 − 私募的 0 檔；有 9/23 換手率的 100 檔，enriched 與重算值的最大差 0.0。
      - https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm（資料日期 09/23）：3055 為 25.39%、2434 為 17.69%、3042 為 10.46%、2033 為 7.27%、3229 為 6.19%、2444 為 5.06%、6668 為 4.50%。
      - verify-pp/cross.py，輸出在 cross.out，排行中有股本資料的 43 檔：符合已發行股數的 33 檔，符合已發行 − 私募的 4 檔（3055、3042、2033、6668），兩者都不符的 6 檔。不符的 6 檔中，2434、3229、2444 有私募股：3229 反推 32,245,202 股，已發行 − 私募為 20,912,493 股；2444 反推 101,638,439 股，已發行 − 私募為 85,426,660 股。3605、3016、2464 沒有私募股，第三方分母仍不等於官方已發行股數。
      - https://tw.stock.yahoo.com/quote/2434.TW/profile：已發行普通股數 37,000,000，市值 1,145.8 百萬元，除以 67.4 元得 17,000,000 股。https://tw.stock.yahoo.com/quote/3055.TW/profile：市值 20,080.159 百萬元，除以 203.5 元得 98,674,000 股。
      - https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=G0400001&flno=43-8：私募應募人「除有左列情形外，不得再行賣出」；交付滿 1 年後、第 3 年內可依持有期間與數量限制轉讓，滿 3 年後解除限制。
      - 本地 data/taiwan/daily/date=2026-09-23 共 1330 筆、enriched 共 1323 筆，全部是 TWSE，沒有 TPEX 標的。

### [P3 → P3] 成交量以「股」加萬／億顯示，且不標單位；台股慣例以張為單位（部分成立）

- **主張**：StockInfoBar 的成交量與量MA5／量MA10 直接格式化股數，例如 6,425,491 股顯示為「642.55萬」，沒有「股」字
- **正確的定義或數值**：以張顯示（例如 6,425 張），或明確標示「股」
- **app 實際行為**：顯示「642.55萬」，容易被誤讀成 642 萬張
- **對使用者的影響**：開啟成交量或量MA欄位時容易誤讀（預設隱藏）
- **修法建議**：台股成交量除以 1,000 並加上「張」，或加上「股」字
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/StockInfoBar.tsx:160
  - frontend/src/lib/format.ts:14-19
  - frontend/src/components/stock-table/primitives.tsx:108-109
  - frontend/src/lib/stock-info-fields.ts:28（預設隱藏）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW：成交量 5,909 張；https://histock.tw/stock/2454：成交量 5,902
- **本地計算證據**：
  - app daily 2454 在 9/23 的 volume = 6,425,491（股）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：台股成交量在資料層以「股」儲存，前端多處直接把股數格式化成萬／億，而且沒有標示單位。

- StockInfoBar 的「成交量」（StockInfoBar.tsx:160，用 fmtVolume，預設隱藏）：2454 在 9/23 的 6,425,491 股顯示為「642.55萬」。
- 日K圖成交量副圖的「量／VOL5／VOL10」（EChartsCandlestick.tsx:183、393-394，用 fmtVol；副圖預設開啟）：顯示「643萬／923萬／791萬」。
- 自選表的「量MA5／量MA10」（primitives.tsx:108-109，用 fmtBigNum，預設隱藏）：顯示「923萬／791萬」。

同一個 app 的 TaiwanScreener 已經換算成「張」，前端口徑不一致。

另外，這個總量依官方 STOCK_DAY 口徑含零股、盤後定價與鉅額交易，和 Yahoo／HiStock／MIS 顯示的一般交易張數（9/23 為 5,902～5,909 張）不同口徑。只除以 1000 標「張」仍會和券商網站差約 9%。建議標「股」，或換算後註明口徑。
    - 理由：核心數值重現成立：本地 parquet 中 2454.TWSE 在 2026-09-23 的 volume = 6,425,491（股），套用 fmtVolume 的邏輯得到「642.55萬」，而且 StockInfoBar 的標籤只寫「成交量」，數值沒有附單位，這點成立。行號引用 StockInfoBar.tsx:160、format.ts:14-19、primitives.tsx:108-109、stock-info-fields.ts:28 我逐一核對過，都正確。

但 finding 有四處不準確或漏掉的面向：

(1) 量MA5／量MA10 不在 StockInfoBar。它們是自選表的欄位，定義在 watchlist-columns.ts:60-61，預設隱藏；由 primitives.tsx:108-109 用 fmtBigNum 顯示，取到整數萬，不是 fmtVolume。以 2454 為例，存檔的 vol_ma5 = 9,226,733.4，顯示「923萬」；vol_ma10 = 7,911,140.7，顯示「791萬」。

(2) 「預設隱藏」低估了影響範圍。日K圖的成交量副圖預設就開啟（StockDailyKChart.tsx:142 `useState(['vol'])`），資訊列的「量」「VOL5」「VOL10」用 EChartsCandlestick.tsx:99-104 的 fmtVol 顯示成「643萬／923萬／791萬」，同樣沒有單位。也就是說，同一個問題在預設畫面上就看得到。

(3) finding 引用的線上數字（Yahoo 5,909 張、HiStock 5,902）和 app 的 6,425,491 股不是同一口徑。官方 MIS 快照在 9/23 13:30 的 v = 5902（張，一般交易整股）；STOCK_DAY 的成交股數 6,430,491 則依官方註記「含一般、零股、盤後定價、鉅額交易」。所以 app 的值除以 1000 得到 6,425 張，仍然和 Yahoo 差約 9%。這批線上數字只能證明台股習慣用「張」，不能拿來比對數值。依它的修法直接標「張」，數字仍會和券商網站對不上；因為這個總量含零股，較穩妥的做法是標「股」，或註明口徑。

(4) app 內部的單位並不一致。TaiwanScreener.tsx:321-325 的 formatVolumeLots 已經換算成「X 張」，RuleEditor.tsx:322-329 也提供張／股切換；只有資訊條、日K副圖和自選表的均量沒有單位。

旁支觀察（不屬於本 finding，未深查）：app 存的 2454 成交量和官方不一致。9/23 是 6,425,491 對官方 6,430,491（少 5,000 股），9/22 是 12,063,543 對 12,131,543（少 68,000 股），成交金額也同步較少。其他 8 檔自選股 9/21 至 9/23 都完全一致。我推測是漏了部分交易類別，例如鉅額交易，這點尚未驗證。

嚴重度維持 P3：這是顯示與可讀性問題，資料本身沒有被破壞。
    - 證據：
      - frontend/src/components/StockInfoBar.tsx:160 — case 'volume': return fmtVolume(Number(latest.volume))，沒有單位
      - frontend/src/lib/format.ts:14-19 — fmtVolume：≥1e4 時 (v/1e4).toFixed(2)+'萬'，沒有「股」
      - frontend/src/lib/stock-info-fields.ts:28 — builtin:volume，label 為「成交量」，visible:false
      - frontend/src/components/stock-table/primitives.tsx:108-109 — vol_ma5／vol_ma10 用 fmtBigNum（整數萬），屬於自選表，不在 StockInfoBar
      - frontend/src/lib/watchlist-columns.ts:60-61 — 量MA5／量MA10 是自選表欄位，visible:false
      - frontend/src/components/StockDailyKChart.tsx:142 — useState<string[]>(['vol'])，成交量副圖預設開啟
      - frontend/src/components/EChartsCandlestick.tsx:99-104,183,393-394 — 副圖資訊列的「量」「VOL5」「VOL10」用 fmtVol（整數萬），沒有單位
      - frontend/src/pages/TaiwanScreener.tsx:321-325 — formatVolumeLots 已換算成「X 張」，app 內口徑不一致
      - backend/app/taiwan/daily_store.py:37-42 — DEFAULT_TAIWAN_METADATA 的 volume_unit=VolumeUnit.SHARES（存檔單位為股）
      - 本地計算：uv run python ...\audit\work\verify-volunit\compare.py → 2454.TWSE 2026-09-23 app_vol=6,425,491, fmtVolume=642.55萬, lots=6,425, fmtBigNum=643萬；finding 的 6,425,491／642.55萬 都重現
      - 本地計算：verify-volunit\volma.py → kline_daily_enriched date=2026-09-23 的 2454.TWSE vol_ma5=9,226,733.4（顯示 923萬）、vol_ma10=7,911,140.7（顯示 791萬），與我從 taiwan/daily 重算的結果一致
      - 官方參考 raw\twse_stock_day_2454_202609.json：fields 為「成交股數」，9/23 是 6,430,491；notes 寫明「當日統計資訊含一般、零股、盤後定價、鉅額交易」
      - 官方參考 raw\mis_snapshot.json：2454 在 d=20260923、t=13:30:00 的 v=5902（張），和 finding 引用的 HiStock 5,902 同一口徑，和 app 的 6,425,491 股不同口徑
      - 旁支：app 2454 在 9/22 為 12,063,543 對官方 12,131,543（−68,000），9/23 為 6,425,491 對 6,430,491（−5,000）；其餘 8 檔自選股 9/21 至 9/23 差異為 0（compare.py 輸出）

## 確認正確的項目

- **volume_units：台股成交量單位為股、A 股為手×100；換手率 = 成交股數 × 100 ÷ 分母（百分數值）**：讀程式碼。把官方 STOCK_DAY 與官方股本餵給純函式 compute_enriched，並把最後一日標成 today；2379、2454、3034、3545、6196 的 turnover_rate 與「官方成交股數 ÷ 官方已發行普通股數 × 100」差距 ≤1.1e-16。指令：uv run python compare3.py
  - 來源：backend/app/volume_units.py:19-30
  - 來源：backend/app/indicators/pipeline.py:734-744
  - 來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a（「當日交易的總股數與上市的總股數之比」）
  - 來源：scratchpad\audit\work\A06\compare3.out
- **TaiwanShareCapitalStore 股本（data/taiwan/share_capital.parquet）與官方 t187ap03_L、mopsfin_t187ap03_O 比對**：逐檔 full join：官方 1,988 列、app 1,988 列，雙向缺漏 0、股數不一致 0。2454 = 1,604,952,613、6196 = 220,329,529；Yahoo 基本資料頁的已發行普通股數同為 1,604,952,613。指令：uv run python compare1.py
  - 來源：backend/app/taiwan/share_capital_store.py:106-134
  - 來源：reference/csv/twse_t187ap03_L_share_capital.csv
  - 來源：reference/csv/tpex_mopsfin_t187ap03_O_share_capital.csv
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/profile（已發行普通股數 1,604,952,613）
- **分母只含普通股、不含特別股**：用實收資本額驗算：面額 10 元的公司中，上市 1,073/1,073、上櫃 877/878 檔符合「實收資本額 = (普通股 + 特別股) × 10」，唯一例外 8088 只差 9 元。所以 issued_common_shares 不含特別股（例如 1101 另有 2 億股特別股）
  - 來源：reference/csv/twse_t187ap03_L_share_capital.csv
  - 來源：reference/csv/tpex_mopsfin_t187ap03_O_share_capital.csv
- **enriched turnover_rate（2026-09-23，上市全市場）**：以 MI_INDEX 成交股數 ÷ 官方股本獨立重算：app 有值的 1,087 檔中，1,066 檔完全相同；其餘 21 檔差異全部來自成交量偏少（見 findings），公式本身沒有錯。自選 2379 = 0.948032%、3034 = 0.466788%、3545 = 0.185321%、6196 = 0.805123%，與獨立計算相同
  - 來源：data/kline_daily_enriched/date=2026-09-23\part.parquet
  - 來源：scratchpad\audit\work\A06\compare1.out
- **與第三方週轉率比對（MoneyDJ／富邦 eBroker 週轉率排行，9/23）**：前 25 名中有 23 檔上市個股：18 檔與 app 差距 ≤0.006 個百分點，例如 3094 為 32.65% 對 32.6526%、2409 為 12.57% 對 12.5697%；其餘 5 檔的差異來自分母口徑，見 findings。第三方的「成交張數」等於官方總成交股數 ÷ 1,000（含零股），例如 3094 為 27,140 張、官方 27,139,876 股，證實分子採總成交股數
  - 來源：https://fubon-ebrokerdj.fbs.com.tw/Z/ZG/ZG_BD.djhtm
  - 來源：scratchpad\audit\work\A06\compare4.out
- **市值 = 收盤價 × 已發行普通股數（K 線資訊條 stock_info.total_shares）**：讀程式碼：kline.py:163-195 只對證券主檔為 stock 的標的提供 total_shares，StockInfoBar.tsx:146 計算 close × total_shares。獨立計算 2454 = 5185 × 1,604,952,613 = 8,321,679,298,405；6196 = 549 × 220,329,529 = 120,960,911,421。三方比對：Yahoo 顯示 2454 為 8,308,176.1 百萬 = 5180（前一日收盤）× 1,603,895 張，6196 為 117,876.015 百萬 = 535（前一日收盤）× 220,329 張，兩者都完全吻合「前一日收盤 × 張數」，所以差異是 Yahoo 落後一日（2454 另有股數較舊的情形），不是 app 算錯。定義依 Wikipedia：MC = N × P
  - 來源：backend/app/api/kline.py:163-195
  - 來源：frontend/src/components/StockInfoBar.tsx:144-158
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/profile
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW/profile
  - 來源：https://en.wikipedia.org/wiki/Market_capitalization（"MC = N × P"）
- **change_pct、change_amount、amplitude（非除權息日）**：以官方 STOCK_DAY 獨立重算：漲跌幅 = 收盤 ÷ 前一日收盤 − 1，振幅 = (高 − 低) ÷ 前一日收盤，並與官方漲跌價差比對。10 檔 × 37 日共 366 列，最大差異都是 0。三方比對：Yahoo 2454 顯示 +0.10%、振幅 2.41%，app 為 0.000965 與 0.024131；Yahoo 6196 顯示 +2.62%、振幅 4.49%，app 為 0.026168 與 0.044860
  - 來源：backend/app/indicators/pipeline.py:503-517
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW
  - 來源：https://xshelp.xq.com.tw/XSHelp/?HelpName=%E6%8C%AF%E5%B9%85&group=FPRICE（振幅 = (最高 − 最低) × 100 ÷ 參考價）
  - 來源：scratchpad\audit\work\A06\compare3.out
- **各層單位換算（漲跌幅、振幅、換手率）**：MIS 與 Yahoo 的 change_pct 是百分數（mis_provider 為 change/prev × 100），在 watchlist_enrichment.py:54-68 與 quote_service.py:778-783 除以 100 轉成小數；enriched 內都是小數；前端 fmtPct（format.ts:8-12）乘 100 後加 %；振幅在 primitives.tsx 乘 100 後加 %；換手率 enriched 存百分數值，前端 toFixed(2)% 直接顯示（Watchlist.tsx:590、1803-1804）。這與 CONTRIBUTING.md 第 3.1 節一致。MIS 快照 6196 的 z=549、y=535 算得 2.6168%，與 Yahoo 相同
  - 來源：backend/app/taiwan/watchlist_enrichment.py:54-68
  - 來源：backend/app/services/quote_service.py:778-783
  - 來源：frontend/src/lib/format.ts:8-12
  - 來源：CONTRIBUTING.md:85-96
- **前端 resolveTurnoverRate 與 StockInfoBar 的振幅、漲跌**：台股 float_shares 為 null 時，直接用後端的 turnover_rate（turnoverRate.ts:10-16）；A 股公式為 volume(手) × 100 ÷ float × 100，換算成股後正確。StockInfoBar 以 close ÷ (1 + change_pct) 還原前一日收盤，再算漲跌與振幅（StockInfoBar.tsx:135-140、161-166），與後端 prev_close 口徑一致
  - 來源：frontend/src/lib/turnoverRate.ts:10-16
  - 來源：frontend/src/components/StockInfoBar.tsx:135-166
- **成交值（amount）單位為新台幣元，非缺漏日與官方一致**：8/03～9/15 共 32 個交易日（10 檔），app daily 的 amount 與 STOCK_DAY 成交金額逐列相同（差異 0）；9/16 之後的差異見 findings
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
  - 來源：scratchpad\audit\work\A06\compare2.out
- **台股歷史換手率 fail-closed（非今日為 null）與流通值不顯示**：屬設計決策，記錄在 docs/taiwan-share-capital-phase-6h.md:106-152；share_capital.py:39-51 會把非今日的台股列設為 null。用純函式實跑確認非今日列為 null；kline.py:195 的 float_shares 為 None，所以不顯示流通值
  - 來源：backend/app/share_capital.py:39-51
  - 來源：docs/taiwan-share-capital-phase-6h.md:106-152
- **換手率顯示精度與自選頁分檔顏色（5/10/20/35%）**：9/23 台股換手率分布：中位數 0.155%、p90 1.84%、p99 11.5%；有 36/1,087 檔 ≥5%、15 檔 ≥10%，分檔顏色仍能區分熱門股。toFixed(2) 與第三方的兩位小數一致
  - 來源：frontend/src/pages/Watchlist.tsx:69-77
  - 來源：data/kline_daily_enriched/date=2026-09-23\part.parquet

## 無法驗證的項目

- **8069 的正確股本**：兩份官方資料相差 17,000 股：mopsfin_t187ap03_O 為 1,154,678,380，dailyQuotes 的「發行股數」為 1,154,695,380，無法確定哪一份較新。app 採用前者，換手率影響約 0.0015%，可忽略
- **MIS 欄位 v（累積成交量）的精確涵蓋範圍**：沒有官方文件。只能由數值推論只含盤中整股：2454 的 v 為 5,902 張、HiStock 同為 5,902、Yahoo 為 5,909，官方為 6,430,491 股。是否含盤後定價無法查證
- **除權息日盤中即時報價（MIS 的 y）是否為參考價**：現在不是除權息日的盤中，無法實測。推測 y 為參考價，因此盤中即時漲跌正確、盤後發佈後才出錯
- **收盤後到午夜之間，K 線資訊條被即時K棒（MIS 量）覆蓋**：依規定不能啟動後端，只能由程式碼推論（kline.py:533、571、582、590），標為 plausible
- **TDR 在台實際掛牌單位數**：官方資料只提供「TDR 原股發行股數」，沒有下載 TDR 流通單位數，無法定量比對
- **自選個股在第三方網站的週轉率（2454、6196 等）**：Yahoo 與 HiStock 個股頁沒有週轉率欄位；Goodinfo 回應 403；鉅亨週轉率排行由前端動態產生，抓不到。只能用 MoneyDJ 前 25 名做三方比對
- **7812（創新板）不在 daily store**：屬股票清單（universe）領域，只觀察到 9/23 MI_INDEX 有此檔而 app 沒有，未深入追查
