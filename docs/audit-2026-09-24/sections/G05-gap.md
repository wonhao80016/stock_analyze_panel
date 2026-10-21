# G05-gap｜G5-frontend-chart-calcs

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 11 | 0 | 0 | 0 | 0 | 14 | 5 |

## 稽核者摘要

共發現 11 項問題：P1 一項、P2 四項、P3 六項。

最嚴重的一項（P1）是除權息日的漲跌基準用錯。個股分時圖的昨收線、右側百分比軸、線色、漲跌停線，以及日K資訊列的漲跌幅，都拿前一根日K的原始收盤價當基準，沒有用官方參考價。自選股 6 次除權息事件裡有 2 次漲跌方向畫反：
- 6196 在 6/24：畫面 -0.18%，官方 +0.93%。
- 3545 在 8/04：畫面 -0.40%，官方 +1.22%。
- 2379 在 9/08：漲跌停線畫在 801.9／656.1，官方是 774／634。

P2 四項：
1. 盤中「量比N」沒有依開盤經過時間折算，顯示值等於標準量比乘上已開盤比例。10:00 時只剩標準值約 22%。後端對同一根即時K棒已經算好折算後的 vol_ratio_5d，但圖表沒有用。
2. 分時「均價」的公式本身正確（累計成交金額÷累計成交量），但分鐘棒只涵蓋錄製程式有在跑的時段。9/23 只有 09:32–11:38 加上 13:27/28 一根，分鐘量只占官方日量 21%～55%。收盤時的均價和 Yahoo 奇摩的均價相比：2454 +24.6bp、2379 -35.5bp、6196 +24.9bp。36 個「個股×交易日」組合中，和官方全日均價最大偏差 +100bp（2454 在 9/22）。
3. 分時圖的金額格式化把 1e9（10億）當成「1億」，10億以上的分鐘成交額顯示小 10 倍。實測 4 根，例如 2454 在 9/18 12:50 的 58.94億顯示成「5.89億」。
4. 日K成交量輸入在 9/16–9/23 有 11/342 列比官方 STOCK_DAY 少，例如 6196 在 9/17 少 17%。這讓 VOL5 最多偏 2.7%，「量比1」最多偏 20%。此項可能與日K資料領域重疊。

P3 六項：
- 00885、00893 是含國外成分的 ETF，沒有漲跌幅限制，圖上仍畫 ±10% 漲跌停線。
- 漲跌停線沒有依升降單位取整，例如 2454 畫 5698／4662，官方是 5695／4665。
- 量以「股」配萬／億顯示，台灣慣例是「張」。
- 迷你分時的均線用收盤價簡單平均，橫軸按資料點等距排列，和個股分時圖不一致。
- connectNulls 會把沒錄到的時段連成直線，看不出缺口。
- 資訊列換手率的公式沿用 A 股「手」的換算，多乘 100（台股目前流通股數為空，暫時不會顯示）。

確認正確的部分：
- 前端 VOL5／VOL10、資訊列均量、量比5（已收盤日），與後端 vol_ma5／vol_ma10／vol_ratio_5d 比對 118～123 天 × 9 檔，誤差為 0。
- 資訊列在非除權息日的漲跌幅與官方比對 339 天，誤差為 0。
- 分鐘時間：kline_minute 的 parquet 是 UTC，API 序列化成 +00:00，formatMinuteTime 加 8 小時後，3744/3744 根全部落在 09:00–13:30 網格內。
- 迷你K棒顏色：108 根，0 處不符。
- 大盤指數條：漲跌點數與漲跌幅公式與官方一致（加權指數 9/23 為 +357.12／+0.75%，與 MI_INDEX、Yahoo 相同；櫃買指數 37 天誤差為 0）。
- 即時K棒修補（patchLiveCandles）不帶成交量，但唯一使用者是迷你K，不讀成交量，沒有實際影響。

本次沒有修改 repo 任何檔案。腳本與輸出都放在 scratchpad\audit\work\G05-gap\，網路請求共 12 次。

## 問題清單

### [P1 → P1] 除權息日：分時圖昨收線、百分比軸、線色、漲跌停線與日K資訊列漲跌幅都用前一日原始收盤價，沒有用參考價（確認成立）

- **主張**：個股分時圖的 prevClose 取自前一根日K的原始收盤價；日K資訊列漲跌幅也用前一根收盤計算；多日分時的 prev_close 同樣取前一日原始收盤價。除權息日的官方基準是除權息參考價（開盤競價基準），所以這幾處在除權息日的漲跌幅、方向與漲跌停線都錯。自選頁迷你分時用的是 MIS 的 y（參考價），同一檔在兩個畫面顯示的漲跌方向可能相反。
- **正確的定義或數值**：除權息日以參考價（TWT49U 開盤競價基準，或 MIS 的 y）作為昨收線、百分比軸、線色與漲跌停的基準；日K資訊列漲跌幅也應和官方或 Yahoo 口徑一致。
- **app 實際行為**：用前一日原始收盤價，漲跌幅偏差等於股利÷前收，最多約 3.4 個百分點（2379），6 次中有 2 次方向相反。
- **對使用者的影響**：自選股每年約 1～4 次除權息。當天打開個股分時會看到『下跌、綠線』，實際上是上漲；漲跌停線也畫錯。和自選頁迷你分時（用 MIS 參考價）互相矛盾。
- **修法建議**：分時圖改用 /api/kline/minute 回傳的 prev_close，並讓後端在除權息日回傳參考價（可由 TWT49U 或當日 MIS 的 y 取得）；日K資訊列改用後端的 prev_close／change_pct，後端 change_pct 同步改為以參考價計算（可能與日K指標領域重疊）。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/StockPanel.tsx:125-129（prevClose = rows[selectedIdx - 1].close）
  - frontend/src/components/EChartsIntraday.tsx:104-110（昨收線）、:336、:347（百分比軸）、:425-427（線色）、:151-159（漲跌停模式以 prevClose 對稱）
  - frontend/src/components/EChartsCandlestick.tsx:907-927、1171-1177（chgPct = (close - prev.close)/prev.close）
  - backend/app/api/kline.py:274-310（_get_previous_closes 取前一日 daily close）→ EChartsMultiDayIntraday.tsx:398-400
- **線上證據**：
  - 官方 TWT49U（reference csv/twse_twt49u_20260601_20260923.csv，下載自 twse.com.tw）：2379 在 2026-09-08 的除權息前收盤 729.0、參考價 704.0、開盤競價基準 704.0，漲停 774.0、跌停 634.0。官方漲跌停是由參考價 ±10% 算出。
  - 官方 STOCK_DAY 在除權息日的漲跌欄是『X0.00』（不比價），不是和前收比較（manifest 的 twse_stock_day limitations）。
  - https://tw.stock.yahoo.com/quote/2454.TW：『昨收 5,180』，等於 MIS 的 y（參考價欄）。
- **本地計算證據**：
  - 指令：python（exdiv_compare.txt）用 kline_daily_enriched 與 TWT49U 比對 6 次自選股除權息事件。
  - 6196 在 2026-06-24：收 540；畫面 -0.18%（基準 541），官方 +0.93%（基準 535），方向相反。
  - 3545 在 2026-08-04：收 49.9；畫面 -0.40%（基準 50.1），官方 +1.22%（基準 49.3），方向相反。
  - 2379 在 2026-09-08：畫面 -5.08%，官方 -1.70%。00878 在 08-18：-4.06% 對 -1.10%。2454 在 07-07：-2.30% 對 -1.71%。0050 在 07-21：+3.33% 對 +3.96%。
  - 2379 在 9/08 的分時漲跌停線：getLimitPrices(729) = 801.9／656.1，官方 774／634（node limit_harness.ts）。
  - 後端 enriched 的 change_pct 在這 6 天和前端值相同（也是用前收），根本原因是資料層沒有保存參考價。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：主張成立。除權息日的基準價，在前端（個股分時的昨收線、百分比軸、線色、資訊列漲跌額、漲跌停價，以及日K資訊列的漲跌幅）和後端（多日分時的 prev_close、enriched 的 change_pct）一律是前一日的原始收盤價。台股日K存的是未還原價，而交易所公告的漲跌停與 Yahoo 奇摩的昨收／漲跌都以除權息參考價為基準，所以除權息日的漲跌幅會偏差「股利÷前收」；已查 6 次事件中有 2 次方向相反，漲跌停價也錯。

需補充三點：
- 漲跌停虛線只在使用者手動切到「漲跌停」模式時才畫出；預設的自適應模式改為用錯誤的漲跌停價限制 Y 軸範圍。
- 修正不能只讓前端改用 /api/kline/minute 的 prev_close，因為後端 _get_previous_closes 回傳的也是原始前收，必須在資料層取得參考價（TWT49U 的開盤競價基準，或當日 MIS 的 y）。
- 「MIS 的 y 在除權息日等於參考價」只由 Yahoo 的行為間接支持，沒有直接驗證，屬於推測。
    - 理由：這條 finding 成立，嚴重度維持 P1。

程式碼部分，我逐一讀了它引用的位置，行號都沒有偏移。個股分時圖的 prevClose 取自前一根日K的 close（StockPanel.tsx:125-129）。這個值傳進 EChartsIntraday 之後，昨收虛線、百分比軸、線色、資訊列漲跌額，以及 getLimitPrices 算出的漲跌停價，都以它為基準。台股的 price_limit.limit_up／limit_down 永遠是 None：本機 data/instruments 目錄是空的，而且 kline.py 只在查當天時才去讀 instruments，所以前端一律用 prevClose×(1±10%) 自己算。日K資訊列的兩處也都用 prev.close。多日分時的 prev_close 來自 _get_previous_closes，取的是前一日日K的 close。

資料層部分，台股的 kline_daily_enriched 存的是未還原的原始價，不是 CONTRIBUTING 3.2 所說的前復權。證據是 2379 在 2026-09-07 的 close 為 729.0，和官方 TWT49U 的「除權息前收盤價」729.0 一致。所以在除權息日，前端拿到的「昨收」就是原始前收，不是參考價。

正確的規則，我用和 finding 不同的來源查證：
1. Yahoo 奇摩股市的 1808 潤隆，資料時間 2026/09/23 13:30，也就是它的除息日。頁面顯示「昨收 33.50」「漲跌 1.60」「4.78%」。官方 TWT49U 同日的除息前收盤是 35.0，參考價和開盤競價基準都是 33.5。可見市場慣用的口徑是以參考價當昨收；若照本 app 的算法會得到 -8.86%。
2. winvest.tw 的教學文章說，除權息當天「開盤價會先做一個調整，這個調整後的價格，我們就叫它『參考價』」，漲跌幅「根據這個新價格來算」。
3. 官方 TWT49U 的漲跌停價是由參考價 ±10% 再依升降單位取整得到：2379 為 704 → 774／634，不是由 729 算出。

我用本機 enriched 資料和 TWT49U 重算了 6 個事件，數值和 finding 完全一致。其中 6196（06-24）與 3545（08-04）畫面顯示下跌，實際上是上漲，方向相反。2379 在 9/08 的漲跌停：app 算出 801.9／656.1，官方是 774／634。

需要修正或補充的細節：
- 漲跌停虛線只有在使用者切到「漲跌停」模式時才會畫出，預設的「自適應」模式不畫。不過自適應模式也用錯誤的漲跌停價來限制 Y 軸範圍，所以在接近真實跌停的極端情況下，價格可能被截在圖外。這是推測，沒有實跑驗證。
- 「MIS 的 y 在除權息日等於參考價」這點，我只確認了 mis_provider.py:160 把 y 當成 prev_close；因為不能連 MIS，無法親自驗證 y 在除權息日的值，只能由 Yahoo 的行為間接推論，屬於推測。
- 修法建議裡「改用 /api/kline/minute 回傳的 prev_close」單獨做沒有用：後端 _get_previous_closes 回傳的也是原始前收，必須連同後端一起改成參考價。

使用者情境：自選股每年都會除權息，例如 00878 每季一次。只要在除權息當天打開個股分時或日K資訊列，就一定會發生。這屬於 CONTRIBUTING 第 11 節定義的「錯誤金融口徑」，所以 P1 恰當。它不影響交易結果或資料寫入，因此不到 P0。

附帶觀察（範圍外，推測是資料缺口）：本機 kline_daily_enriched 和 data/taiwan/daily 都沒有 2026-07-10 分區，但官方 TWT49U 記錄 3034 在這天除息，也就是說當天有交易。
    - 證據：
      - frontend/src/components/StockPanel.tsx:125-129 — prevClose = rows[selectedIdx - 1].close，並在 :186 傳入 StockIntradayChart
      - frontend/src/components/StockIntradayChart.tsx:127-133 — 把 prevClose 原樣傳給 EChartsIntraday；priceLimit 來自 minute.data.price_limit
      - frontend/src/components/EChartsIntraday.tsx:104-110（昨收虛線）、:151-181（漲跌停與自適應 Y 軸都以 getLimitPrices(prevClose) 計算）、:336、:347（百分比軸）、:425-427（線色）、:532（資訊列漲跌額）；:44-62 在 limit_up 為空時用 prevClose×(1±rate) 自己算；:413 預設 yMode='adaptive'
      - frontend/src/components/EChartsCandlestick.tsx:907-927 與 :1171-1177 — chgPct = (close - prev.close)/prev.close
      - frontend/src/components/EChartsMultiDayIntraday.tsx:398-401 — changePct 與顏色以 session.prev_close 計算
      - backend/app/api/kline.py:274-315 — _get_previous_closes 取前一交易日的 daily close；:213-271 — 只有 trade_date == cn_today() 時才讀 instruments 的 limit_up/limit_down，而 data/instruments 目錄是空的，所以台股永遠回 None
      - backend/app/taiwan/realtime/mis_provider.py:160 — prev_close = item.get('y')（自選頁迷你分時的 prevClose 來源，見 frontend\src\pages\Watchlist.tsx:1859）
      - 本機讀取（uv run python read_enriched.py，唯讀 parquet）：kline_daily_enriched 的 2379 在 2026-09-07 close=729.0、09-08 close=692.0／prev_close=729.0／change_pct=-0.05075；6196 在 06-24 為 540／541；3545 在 08-04 為 49.9／50.1。可見存的是未還原價
      - 官方參考資料 reference\csv\twse_twt49u_20260601_20260923.csv：2379 在 2026-09-08 的除息前收盤 729.0、參考價 704.0、開盤競價基準 704.0、漲停 774.0、跌停 634.0；1808 在 2026-09-23 的除息前收盤 35.0、參考價 33.5
      - 本機重算（uv run python calc.py）：app% 對 official%：2379 -5.08 對 -1.70；6196 -0.18 對 +0.93；3545 -0.40 對 +1.22；00878 -4.06 對 -1.10；2454 -2.30 對 -1.71；0050 +3.33 對 +3.96；getLimitPrices(729)=801.9/656.1
      - https://tw.stock.yahoo.com/quote/1808.TW（抓取時間：台北 2026-09-24 06:13 開盤前，資料時間 2026/09/23 13:30，也就是 1808 的除息日）：『昨收 33.50』『漲跌 1.60』『4.78%』『成交 31.90』。昨收等於官方參考價 33.5，不是原始前收 35.0
      - https://winvest.tw/Knowledge/Article/232：『你的股票在除權或除息的這天，它的「開盤價」會先做一個調整，這個調整後的價格，我們就叫它「參考價」』；並說漲跌幅『根據這個新價格來算』
      - reference\manifest.json 第 247 行：官方 STOCK_DAY 在除權息日的漲跌價差是 X0.00（不比價），不是 0
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：有四處都拿前一日的原始收盤價當基準，而且資料層沒有前復權（data\adj_factor 為空，close = raw_close）：個股分時圖的昨收線、百分比軸、線色與漲跌停 Y 軸（StockPanel.tsx:125-129 → EChartsIntraday）；日K資訊列漲跌幅（EChartsCandlestick.tsx:907-927、1171-1177）；多日分時的 prev_close（kline.py:274-310）；後端 enriched 的 change_pct。除權息日的正確基準是官方參考價，也就是 TWT49U 的開盤競價基準，官方漲跌停就是由它 ±10% 再依升降單位取整算出。所以除權息日的漲跌幅少算了約「股利 ÷ 前收」，偏差 0.6～3.4 個百分點。自選股 6 次事件中有 2 次方向相反：6196 在 06-24 畫面 -0.18%，以參考價計算應為 +0.93%；3545 在 08-04 畫面 -0.40%，應為 +1.22%。另外 2379 在 09-08 的漲跌停線畫成 801.9／656.1，官方是 774／634。補充三點：（1）交易所 STOCK_DAY 在除權息日的漲跌是 X0.00（不比價），所以「官方 +0.93%」應理解為「以官方參考價計算的值」；（2）本地分鐘K只有 9/17 到 9/23 四天，分時圖這部分只會出現在未來除權息日當天，日K資訊列的錯誤則在歷史 6 天都看得到；（3）修正時除了換成參考價，getLimitPrices 也要改成依台股升降單位取整。只換基準會得到 774.4／633.6，仍與官方 774／634 不符。自選頁迷你分時用 MIS 的 y。「除權息日的 y 等於參考價」是由 u／w 與 TWT49U 推得，參考資料裡沒有直接觀察到。
    - 理由：我用官方 TWT49U、STOCK_DAY 參考資料與本地 kline_daily_enriched parquet 重算。finding 列的 6 次自選股除權息事件，數值全部照原數字重現，程式碼路徑也逐一核對屬實。

【數值並列：finding → 我的重算】
- 6196（06-24）：畫面 -0.18% → -0.18%；參考價基準 +0.93% → +0.93%（基準 535）或 +1.02%（基準 534.55）。
- 3545（08-04）：-0.40% → -0.40%；+1.22% → +1.22%（基準 49.3）或 +1.26%（基準 49.28）。
- 2379（09-08）：-5.08% → -5.08%；-1.70% → -1.70%。
- 00878（08-18）：-4.06% → -4.06%；-1.10% → -1.10%。
- 2454（07-07）：-2.30% → -2.30%；-1.71% → -1.71%。
- 0050（07-21）：+3.33% → +3.33%；+3.96% → +3.96%。
- 其中 2 次方向相反（6196、3545）；最大偏差 -3.37 個百分點（2379），與 finding 一致。
- 漲跌停線：getLimitPrices(729) 算出 801.9／656.1，官方是 774／634，也一致。
- 後端 enriched 的 change_pct（例如 6196 是 -0.001848）等於前一日原始收盤計算的值，而且 close = raw_close。data/adj_factor 是空的，資料沒有前復權，所以無法靠復權自然修正。

【程式碼核對屬實】
- StockPanel 取前一根日K收盤當 prevClose，傳進 StockIntradayChart，再傳進 EChartsIntraday。
- 昨收線、百分比軸、線色、漲跌停 Y 軸都以這個 prevClose 為基準。
- 日K資訊列用 prev.close 算漲跌幅。
- 多日分時的 prev_close 由 _get_previous_closes 取前一日 daily close。
- 自選頁迷你分時的 prevClose 與 changePct 來自 MIS 的 y（mis_provider.py:160）。

【需要修正或補充的細節（不影響主結論）】
1. 「官方 +0.93%」不是交易所公布的數字。TWSE STOCK_DAY 在除權息日的漲跌欄是 X0.00（不比價）。這些百分比是用官方參考價算出來的，屬於市場慣例口徑。真正能直接證明「官方以參考價為基準」的是 TWT49U：2379 的漲停 774、跌停 634，正好是 704 ±10% 再依升降單位取整。
2. Yahoo「昨收 5,180」那條線上證據是 9/23 的一般交易日。STOCK_DAY 顯示 2454 在 9/22 的原始收盤就是 5180，所以這條證據證明不了除權息日的行為。「除權息日 MIS 的 y 等於參考價」是由 u／w 與 y ±10% 的關係加上 TWT49U 推得，參考資料裡沒有直接觀察到。
3. 本地 kline_minute 只有 9/17、9/18、9/22、9/23 四天，都不是自選股除權息日，所以歷史除權息日的分時圖目前看不到。分時圖的錯誤（昨收線、百分比軸、線色、漲跌停線）要等未來除權息日當天盤中查看才會出現；日K資訊列的錯誤現在就能在歷史 6 天看到。
4. Taiwan 的 instruments 是空的，price_limit 一律走規則。即使改用參考價，getLimitPrices 也只四捨五入到 0.01（704 → 774.4／633.6），不依台股升降單位取整。所以只換基準還對不上官方 774／634。

【其他】
網路查證方面：WebSearch 次數已用完。zh.wikipedia「除權」頁沒有台股漲跌基準的說明；Yahoo 歷史頁回傳 404。

【嚴重度】
除權息日的漲跌幅口徑錯誤、方向可能相反、漲跌停線錯位，屬於 CONTRIBUTING 第 11 節的「錯誤的金融口徑」，維持 P1。
    - 證據：
      - 腳本：AUDIT_SCRATCH/work/verify-exdiv/exdiv_verify.py。輸出：exdiv_verify_out.csv、pct_table.txt（同目錄）。指令：cd backend && uv run python exdiv_verify.py
      - 重算結果（收盤／畫面值／以開盤競價參考價計算／差距）：6196 06-24：540／-0.18%／+0.93%／-1.12pp，方向相反；3545 08-04：49.9／-0.40%／+1.22%／-1.62pp，方向相反；2379 09-08：692／-5.08%／-1.70%／-3.37pp；00878 08-18：32.38／-4.06%／-1.10%／-2.96pp；2454 07-07：4030／-2.30%／-1.71%；0050 07-21：102.5／+3.33%／+3.96%
      - kline_daily_enriched（data/kline_daily_enriched/date=YYYY-MM-DD\part.parquet）：6 次事件的 close 都等於 raw_close，也等於 taiwan/daily 的 close；前一日 close 等於 TWT49U 的除權息前收盤（541、4125、99.2、50.1、33.75、729）；enriched 的 prev_close 欄是原始前收，change_pct 是 -0.001848、-0.02303、0.033266、-0.003992、-0.040593、-0.050754，與畫面值一致。data\adj_factor 為空，沒有前復權
      - TWT49U（reference csv\twse_twt49u_20260601_20260923.csv）：2379 在 2026-09-08 的除權息前收盤 729.0、參考價 704.0、漲停 774.0、跌停 634.0、開盤競價基準 704.0；6196 在 06-24 的參考價 534.55、開盤競價基準 535.0；3545 在 08-04 的參考價 49.28、開盤競價基準 49.3。3034 在 07-10 本地沒有分區，所以可比對的事件是 6 次
      - STOCK_DAY（reference csv\twse_stock_day_202608_202609.csv）：2379 在 2026-09-08 的 change_raw 是 X0.00、flag 是 X；3545 在 08-04、00878 在 08-18 也都是 X0.00。交易所本身在除權息日不公布漲跌幅
      - node 計算 getLimitPrices（rate 0.1，limit_up 為 null）：以 729 為基準得 [801.9, 656.1]；以 704 為基準得 [774.4, 633.6]，官方是 774／634（沒有依升降單位取整）
      - frontend/src/components/StockPanel.tsx:125-129 prevClose = rows[selectedIdx - 1].close，:186 傳入 StockIntradayChart
      - frontend/src/components/StockIntradayChart.tsx:126-128 把 prevClose prop 與 minute.data.price_limit 傳給 EChartsIntraday，沒有使用 minute.data.prev_close
      - frontend/src/components/EChartsIntraday.tsx:104-110 昨收線；:151-159 漲跌停模式以 prevClose 對稱；:336、:347 百分比軸；:425-427 線色；:44-63 getLimitPrices 四捨五入到 0.01
      - frontend/src/components/EChartsCandlestick.tsx:907-927 及 :1171-1177，漲跌幅都由 (close - prev.close) / prev.close 計算
      - backend/app/api/kline.py:274-310 _get_previous_closes 取前一日 daily close（docstring 寫 adjusted，實際資料是原始價）；:213-271 _get_price_limit_info 在 instruments 為空時只回傳 rate（data\instruments 目錄為空）
      - frontend/src/components/EChartsMultiDayIntraday.tsx:398-401 與 StockMultiDayIntradayChart.tsx:55 使用 session.prev_close
      - 自選頁：backend/app/taiwan/realtime/mis_provider.py:160 prev_close = y；watchlist_enrichment.py 把 prev_close 與 change_pct 取自 quote；Watchlist.tsx:1859 → MiniIntraday.tsx 優先用 changePct 著色、prevClose 當基準線
      - 限制：data\kline_minute 只有 2026-09-17、09-18、09-22、09-23 四個分區，沒有任何自選股除權息日，所以分時圖的錯誤目前無法在歷史日期重現畫面，只會發生在未來除權息日
      - 線上：WebSearch 次數已用完；https://zh.wikipedia.org/wiki/除權 沒有台股漲跌停基準的說明；https://tw.stock.yahoo.com/quote/2379.TW/history 回傳 HTTP 404。finding 引用的 Yahoo「昨收 5,180」屬於一般交易日，STOCK_DAY 顯示 2454 在 2026-09-22 的收盤是 5180.0，不能證明除權息日的行為

### [P2 → P2] 盤中『量比N』沒有依開盤經過時間折算，即時K棒的量比被系統性低估（確認成立）

- **主張**：volumeRatioAt 直接用當日累計量除以前 N 日的全日均量。盤中即時K棒的量只是部分量，所以顯示值等於標準量比乘上已開盤比例 p。後端注入即時K棒時已附上依 session_progress 折算過的 vol_ratio_5d，但圖表沒有用它，自己重算且不折算。
- **正確的定義或數值**：盤中以（當日累計量 ÷ 已開盤分鐘數）÷（前 N 日每分鐘均量）計算，或直接使用後端的 vol_ratio_5d。
- **app 實際行為**：盤中顯示未折算的比值，例如正常量能的股票在 10:00 約只顯示 0.2～0.3x，要到收盤才正確。
- **對使用者的影響**：使用者開啟即時行情，K線副圖預設開啟量比；盤中最後一根會被持續低估，量能放大判斷失真，且和自選表或選股用的 vol_ratio_5d 不一致。
- **修法建議**：只在最後一根是即時K棒（is_live）時，依 session_progress 折算分子（或 N=5 時直接用 row.vol_ratio_5d）；已收盤日維持現行公式。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/EChartsCandlestick.tsx:106-119（沒有時間折算）、:140（柱上標籤）、:395-402（資訊列『量比N』）
  - backend/app/api/kline.py:560-590（即時K棒帶部分量 volume，並複製 vol_ratio_5d）
  - backend/app/indicators/pipeline.py:1574-1585（time_factor = 1/session_progress）
  - frontend/src/components/StockDailyKChart.tsx:21（預設開啟，N=1）
- **線上證據**：
  - https://wiki.mbalib.com/zh-tw/量比：『量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗』
- **本地計算證據**：
  - 已收盤日：N=5 前端值與後端 vol_ratio_5d 在 9 檔 × 122 天完全相同（maxRelRatio=0），所以差異只出在盤中。
  - 推導：前端值 = V_部分 / avgN；後端值 = V_部分 × (1/p) / avg5，所以前端值 = 後端值 × p。在 10:00，p = 60/270 = 0.222。
  - 稽核時已收盤，沒有即時實測（見 unverifiable）。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立。即時行情開啟且盤中時，/api/kline/daily 最後一根是 is_live 活K棒，volume 是盤中累計的部分量。前端 EChartsCandlestick.volumeRatioAt 直接拿它除以前 N 日的全日均量，沒有依已開盤時間折算，所以預覽日K資訊列預設顯示的「量比1」與柱上標籤都被系統性低估：N=5 時前端值等於後端 vol_ratio_5d 乘上盤中進度 p。這不符合量比的標準定義（以每分鐘均量比較，或與歷史同一時點的累計量比較）。已收盤日前端公式正確（N=5 時與 vol_ratio_5d 完全一致）。

補充與修正三點：
1. 10:00 的「0.2～0.3x」是依時間線性估算（p=0.222）。台股盤中量能前段集中，實際顯示值可能略高，但仍明顯低於 1，本地資料無法實測。
2. 預設 N=1 時，圖表量比即使收盤後也與 vol_ratio_5d 不同，這是窗口設定差異，不屬於本 bug。
3. 修法若依 is_live 分流，要先在 toOHLC 與 OHLC 型別補上 is_live（前端目前完全沒有這個欄位）。另一種做法是 N=5 時直接用 row.vol_ratio_5d；或 N≠5 時由後端提供 session_progress，把分子折算到全天量級。
    - 理由：我逐一讀了它引用的程式碼，行號與現況一致。前端 volumeRatioAt 用當根K棒的成交量，除以前 N 根的全日均量，完全沒有時間折算。即時行情開啟時，後端 _maybe_inject_live_candle 會在 /api/kline/daily 最後補上一根今日K棒（is_live），volume 是盤中累計量（部分量）。後端 compute_enriched_today 會先以 time_factor = 1/session_progress 把部分量折算成全天量級，再算 vol_ratio_5d；這個值雖然有複製進 live_row，但前端圖表完全沒讀它。前端也沒有任何地方用到 is_live，toOHLC 並未帶出這個欄位。預設設定是 enabled:true、days:1，活躍子圖預設為 ['vol']，資訊列預設指向最後一根，所以盤中打開預覽日K時，資訊列的「量比1」與柱上標籤都是「部分量 ÷ 全日均量」。

定義方面，我另外找了兩個與 finding 不同的來源，結論一致：
- 快懂百科：量比是「即時每分鐘平均的成交量」對「過去5個交易日每分鐘平均成交量」。
- TradingView 的 Relative Volume at Time：比較的是「某一時點的累計量」與「歷史上同一時點的平均」。

兩者都要求做時間或同時點的正規化，後端自己的註解也寫明採用這個標準口徑。所以盤中直接拿部分量除以全日均量，不是正確的量比。

本地驗證：已收盤日前端的 N=5 公式與磁碟上的 vol_ratio_5d 完全相同（9 檔、1125 列，最大相對差 0.0），證實差異只出在盤中活K棒；在 p∈(0,1) 時可推得前端值 ＝ 後端值 × p。

嚴重度：這是明確的功能錯誤。它不寫資料、不驅動交易或告警，收盤後自動恢復正確，所以不到 P1；但名稱標示「量比」，盤中會系統性低估，而且和自選表的量比方向相反，判 P2 恰當。

需要修正或補充的細節：
- 「10:00 約 0.2～0.3x」是依時間線性估算（p＝60/270＝0.222）。台股盤中量能通常前段集中，實際累計占比可能更高（推測約 0.3 左右），但仍明顯低估。本地分鐘資料每天從 09:13～10:17 才開始，無法實測。
- 預設 N=1 時，圖表的「量比1」與 vol_ratio_5d 就算在收盤後也不同，因為窗口不同，這是設計上的選擇。真正的新問題只在盤中沒有時間折算。
- 修法若要依 is_live 判斷，需要先在 toOHLC 與 OHLC 型別補上 is_live（前端目前完全沒有這個欄位）。另外，ETF 活K棒要在即時 ETF 拉取開啟時才會注入，但自選中的 5 檔個股在即時行情開啟時一定會受影響。
    - 證據：
      - frontend/src/components/EChartsCandlestick.tsx:106-119 volumeRatioAt：只有 `current / average`（average＝前 window 根的 volume 平均），沒有任何時間或盤中進度參數
      - frontend/src/components/EChartsCandlestick.tsx:140-157 柱上標籤用 volumeRatioAt，show 條件為 volumeCompare.enabled && !compact
      - frontend/src/components/EChartsCandlestick.tsx:395-401 資訊列 `量比${days}` 用同一個函式；:832、:954 的 infoIdxRef 預設為 data.length - 1（最後一根，也就是活K棒）
      - frontend/src/components/StockDailyKChart.tsx:21 DEFAULT_VOLUME_COMPARE = { enabled: true, days: 1 }；:141 activeIndicators 預設 ['vol']；:247-252 下拉選項標為『前N日均量』
      - Grep 在 frontend/src 找 `is_live`：零命中，前端不區分活K棒；Grep `vol_ratio_5d` 只出現在 Watchlist.tsx:591、stock-table 等處，EChartsCandlestick 沒有使用
      - backend/app/api/kline.py:564-584 live_row 的 volume 取 q.get('volume')（盤中累計量）、is_live=True，並複製 vol_ratio_5d；:437/:461/:468 三條 /daily 路徑都會呼叫 _maybe_inject_live_candle
      - backend/app/indicators/pipeline.py:1575-1586 註解寫『标准量比: 今日累计成交量 / (前5日均量 × 当日时段已过比例)』，time_factor = 1/session_progress
      - backend/app/services/quote_service.py:1673-1686 以 quote_ts 的中位數推出 session_progress，傳給 compute_enriched_today；calendar.py:22-31 交易時段 09:00-13:30，共 270 分鐘
      - backend/app/taiwan/realtime/mis_provider.py:165-167 盤中 volume 由張換算成股，與歷史日K單位一致，排除單位不一致造成的偏差
      - 線上（不同於 mbalib）：https://www.baike.com/wiki/%E9%87%8F%E6%AF%94 —『量比是衡量相对成交量的指标，它是以即时每分钟平均的成交量与过去5个交易日每分钟平均成交量进行对比。』
      - 線上：https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/ — 定義為比較『the regular or cumulative volume at a particular moment within a period to its average over similar historical time points』，累計模式『calculates the total volume accumulated since the last period anchor』
      - 本地計算：`cd backend && uv run python scratchpad/audit/work/verify-g5-volratio/minute_share.py` 輸出『closed-day rows: 1125 symbols: 9 max rel diff fe5 vs vol_ratio_5d: 0.0』（fe5 = volume / 前5日 volume 平均，與 enriched 的 vol_ratio_5d 比較）
      - 本地分鐘資料（data/kline_minute 共 4 天）的第一筆落在 UTC 01:13～02:18（台北 09:13～10:18），不是從開盤開始，無法實測 10:00 的真實累計量占比；0.2～0.3x 屬線性推估
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：K線副圖的『量比N』（EChartsCandlestick.tsx:106-119，預設開啟且 N=1）直接用當日累計量除以前 N 日的全日均量，沒有依開盤經過的時間折算。盤中最後一根即時K棒只有部分量，所以顯示值等於同一分母下的標準量比乘上已開盤比例 p（p＝已過分鐘數／270）。

實測重現：
- 已收盤日：前端 N=5 與後端 vol_ratio_5d 在 9 檔 × 122 天完全相同。
- 盤中：以真實 compute_enriched_today 模擬，前端 N=5 ÷ 後端值在各時點精確等於 p（10:00 時為 0.2222）。依均速假設，全日量等於前 5 日均量的股票在 10:00 會顯示 0.22x。實際盤中量集中在開盤與收盤，數值會較高，屬推測。

補充修正：
- 後端的時間折算只在增量路徑（_enrich_today_incremental）成立；退回全量 compute_enriched 時不折算。
- toOHLC 沒有把 vol_ratio_5d 傳進圖表，payload 也沒有 p。修法要先補上這些欄位，才能在 is_live 列改用後端值或乘上 1/p。

另有原記錄沒提到的面向：即時K棒的量用 MIS 的 v（以張為單位），比官方成交股數少 5～8%（9/23 的 2454 為 0.9185、0050 為 0.9520）。所以即使收盤後，在官方日資料取代即時K棒之前，前端與後端的即時量比都仍然偏低同樣的幅度。
    - 理由：我用資料重現了這條 finding 的核心主張，數值完全吻合。

做法：用 node 從真正的 EChartsCandlestick.tsx 抽出 volumeRatioAt 原始碼，以 repo 內的 TypeScript 轉譯後直接執行，沒有自己改寫公式。後端值則照 Repository._build_live_agg 的做法，以 2026-09-22 為基準建出遞推狀態，再呼叫真正的純函式 compute_enriched_today 取得。

一、已收盤日：預設 6 個月區間，9 檔 × 122 天共 1098 筆，前端 N=5 與 enriched 的 vol_ratio_5d 最大相對差為 0，與 finding 的 maxRelRatio=0 相同。

二、盤中：以 2026-09-23 的官方日量乘上 p，模擬「均速成交」下的部分量。在 09:30、10:00、11:00、12:00、13:00 五個時點，9 檔的「前端 N=5 ÷ 後端 vol_ratio_5d」全部精確等於 p（0.1111／0.2222／0.4444／0.6667／0.8889），與 p 的最大偏差是 0，證實「前端值＝後端值×p」。例如 10:00 時，2454 前端 N=5 為 0.1549，後端為 0.6969；0050 為 0.1562 對 0.7029。一檔全日量剛好等於前 5 日均量的股票，在 10:00 會顯示 0.222x。

程式碼證據成立：
- 前端公式沒有做時間折算，資訊列與柱上標籤都用它。
- 即時K棒帶的是部分量，後端也把 vol_ratio_5d 複製進 live_row。
- 預設設定為開啟且 N=1。

需要修正的細節：
(1) 後端「已折算」只在增量路徑（_enrich_today_incremental）成立。退回全量 compute_enriched 的路徑（quote_service.py:1721-1768 → pipeline.py:486-491）不做折算，這時前端 N=5 與後端一樣偏低。
(2) toOHLC（StockDailyKChart.tsx:63-89）沒有帶出 vol_ratio_5d，payload 也沒有 session_progress 欄位。所以修法不能只寫「改用 row.vol_ratio_5d」，必須先把這個欄位傳進圖表，或提供 p。
(3)「10:00 約 0.2～0.3x」是依均速假設（p=0.222）推出的。實際台股盤中量多半集中在開盤與收盤，數值通常會較高，本地分鐘資料不完整，無法實測，這一點屬推測。
(4) pipeline 的行號應為 1575-1586，finding 寫 1574-1585，屬小誤差。

新發現（原 finding 沒有提到）：即時K棒的量來自 MIS 的 v（以張為單位），比官方成交股數少。9/23 收盤時四檔的比例為 2454 0.9185、6196 0.9341、0050 0.9520、00878 0.9446。所以即使到 13:30、p=1，即時K棒的量比（前端與後端都一樣）仍然低 5～8%，要等官方日資料取代即時K棒後才會回正。

嚴重度維持 P2：只影響圖表顯示，不影響選股或交易，但它是標示為「量比」的明確數值錯誤，而且和後端口徑不一致。
    - 證據：
      - frontend/src/components/EChartsCandlestick.tsx:106-119：volumeRatioAt 回傳 current / average，沒有任何時間折算；:140 用於柱上標籤；:395-402 用於資訊列『量比N』
      - frontend/src/components/StockDailyKChart.tsx:21：DEFAULT_VOLUME_COMPARE = { enabled: true, days: 1 }；:142 預設 activeIndicators=['vol']；:63-89 toOHLC 沒有帶 vol_ratio_5d
      - backend/app/api/kline.py:571：live_row 的 volume 取即時累計量；:574 is_live=True；:582-584 複製 vol_ratio_5d
      - backend/app/indicators/pipeline.py:1578-1586：vol_ma5_prev = _vol_ma5_prev_sum/5；盤中 time_factor = 1/session_progress；vol_ratio_5d = volume*time_factor/vol_ma5_prev（finding 寫 1574-1585，差 1 行）
      - backend/app/indicators/pipeline.py:486-491 與 backend/app/services/quote_service.py:1721-1768：全量退回路徑的 vol_ratio_5d = volume/_vol_ma5_prev，盤中也不折算
      - backend/app/taiwan/realtime/calendar.py:22-23,31,143-145：交易時段 09:00–13:30，SESSION_TOTAL_MINUTES=270，session_progress = 已過分鐘數 / 270
      - 本地計算 A（指令：cd backend && uv run python scratchpad\audit\work\verify-volratio\repro.py；前端函式由 run_front.cjs 從 TSX 抽出並以 repo 內的 typescript 轉譯後執行）：9 檔各 127 列（2026-03-24～09-23），比對 122 筆，共 1098 筆，maxRel=0.000e+00，與 finding 相同
      - 本地計算 B（真實呼叫 compute_enriched_today，基準日 2026-09-22，部分量 = 9/23 官方日量 × p）：front_n5/back 在 09:30、10:00、11:00、12:00、13:00 分別精確等於 0.1111、0.2222、0.4444、0.6667、0.8889（max_abs_dev_from_p=0.0000）。10:00 的例子：2454 前端 0.1549／後端 0.6969；3034 0.1887／0.8493；0050 0.1562／0.7029；00878 0.2186／0.9839；N=1 時 2454 為 0.1184，收盤口徑為 0.5326
      - 本地計算 C：9/23 收盤 MIS v（參考資料 csv/mis_snapshot.csv）× 1000 ÷ 官方成交股數（csv/twse_stock_day_202608_202609.csv）：2454 為 5,902,000/6,425,491=0.9185（N5 0.6401 對 0.6969）；6196 為 0.9341；0050 為 0.9520；00878 為 0.9446
      - 線上來源 https://wiki.mbalib.com/zh-tw/量比：『量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗』，已由 WebFetch 取回原文確認
      - 無法實測：抓取當下是台北時間 2026-09-24 06:11，已收盤。kline_minute 只有 4 天且有缺口（09-18 從 09:13 開始，分鐘量覆蓋率 44～87%）；kline_daily 分區的內容與日期錯位（例如 date=2026-09-17 的 0050 量等於 09-16 的量），無法用來重建真實盤中累計量
      - 附帶觀察（不屬本 finding，另行查證）：本地 enriched／taiwan/daily 在 09-16～09-23 有 16 筆日量低於官方 STOCK_DAY，例如 2330 在 09-18 少 3,702,177 股、2454 在 09-23 少 5,000 股；前後端用的是同一份資料，所以不影響本 finding 的比值關係
      - 唯讀確認：git status 與開始時相同（仍是原本 3 個既有修改）；data\kline_daily_enriched\date=2026-09-23\part.parquet 的 mtime 仍為 2026-09-23 22:07

### [P2 → P2] 分時『均價』只涵蓋錄製有在跑的時段，和全日均價偏差最多 100bp，畫面沒有任何標示（確認成立）

- **主張**：computeIntradayAverage 的公式正確，但輸入的分鐘棒有三個缺口：(1) 錄製程式啟動或重啟後，第一筆快照只建立基準、增量為 0，開盤到啟動之間的量全部不計；(2) 快照之間有空檔時，多分鐘的量會併到下一根，並以該根價格估算金額；(3) 中途停錄的時段也缺。畫面把這個值當作當日均價顯示。（分鐘量錯位已由 A13 記錄，這裡只看它對均價的影響。）
- **正確的定義或數值**：當日均價應涵蓋 09:00 起的全部成交，和 Yahoo 或官方的均價一致；如果做不到，就要標示為『觀測區間均價』並顯示涵蓋時段或涵蓋率。
- **app 實際行為**：只用觀測到的片段計算，偏差可達數十 bp 到 1%，畫面沒有標示。
- **對使用者的影響**：個股分時與多日分時的『均價』線和數值，會被當成真實成交均價，用來判斷強弱或設定價位，但它可能偏高或偏低 0.2%～1%。
- **修法建議**：有能提供當日累計成交金額的來源時（例如報價的 amount），用它錨定 Σ金額／Σ量；否則當涵蓋率低於門檻（用分鐘量合計 ÷ 報價累計量）時隱藏均價，或改標為觀測區間均價並顯示起訖時間。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/lib/intraday-chart.ts:16-26
  - backend/app/taiwan/realtime/minute_recorder.py:134-142（第一筆快照增量為 0）、:150、:161（amount = 價格 × Δ量）、:81（非 OPEN 就不記錄）
  - frontend/src/components/EChartsIntraday.tsx:421、591-594（顯示均價）
  - frontend/src/components/EChartsMultiDayIntraday.tsx:66、418
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW（資料時間 2026/09/23 14:30）：『均價 5,192』『成交量 5,909 張』『成交金額 306.82 億元』
  - https://tw.stock.yahoo.com/quote/2379.TW：『均價 772』
  - https://tw.stock.yahoo.com/quote/6196.TW：『均價 546.00』，並註明『不含盤後定價、零股、鉅額、拍賣及標購』
  - https://en.wikipedia.org/wiki/Volume-weighted_average_price：『The indicator is usually computed for a single trading day』
- **本地計算證據**：
  - node harness.ts 加 python（vwap_compare.txt）：9/23 的分鐘棒只有 09:32～11:38，接著跳 110 分鐘到 13:27/13:28 一根，而且那根量為 0（重啟後的基準）。
  - 分鐘量占官方日量的比例：9/23 為 0.211～0.547；4 天合計 0.120～0.866。
  - 9/23 收盤時的畫面均價：2454 為 5204.78（Yahoo 5192，+24.6bp；官方 5192.23）；2379 為 769.26（Yahoo 772，-35.5bp；官方 772.08）；6196 為 547.36（Yahoo 546，+24.9bp；官方 546.77）。
  - 36 個『個股×交易日』組合和官方全日均價比，最大偏差 +100.4bp（2454 在 9/22：5377.20 對 5323.73），9/22 只錄到 11:34。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：computeIntradayAverage 的公式正確，但 minute_recorder 產生的分鐘棒只涵蓋錄製程式有在跑的時段，缺口有三個：(1) 啟動或重啟後第一筆快照只當基準；(2) 中途停錄的空檔（例如 9/23 的 11:38～13:28）；(3) 快照之間的量都用當下價格估算金額。前端卻把累計結果直接標成「均價」顯示。後端回傳的 source='local_partial' 前端沒有使用；connectNulls 還把盤中空檔用直線接起來，畫面上看不出來。實測 36 個「個股×交易日」組合跟官方全日均價（成交金額 ÷ 成交股數）比，偏差從 +100.4bp（2454 在 9/22）到 -46.9bp（3034 在 9/17）；分鐘量只占官方日量的 12%～87%。補充：即使整天沒有中斷，開盤集合競價的量（第一筆盤中快照只當基準）和 13:30 收盤集合競價的量（本機時鐘過了 13:30:00 就不再記錄）在結構上也永遠不會算進去，所以這個均價不可能等於全日均價。要修，只能用官方累計成交金額和成交量來錨定，或者把這個值改標為觀測區間均價，並顯示涵蓋時段。
    - 理由：這條 finding 成立，P2 也合理。我逐項讀了現在的程式碼，並用官方日成交資料重算了所有數字，結果跟 finding 一致。

一、程式碼
- computeIntradayAverage 的公式是對的，就是累計金額除以累計量（intraday-chart.ts:16-26）。問題出在餵給它的分鐘棒。
- 分鐘棒全部來自 minute_recorder 用快照折算出來的結果：
  - 每天第一筆盤中快照只拿來當累計量的基準，增量記為 0（minute_recorder.py:127-142）。
  - 兩筆快照之間的量，全部用當下這筆快照的價格估算金額（:150、:161）。
  - 盤別不是 OPEN 時完全不記錄（:81）。
- 使用者設定是 minute_data_provider='taiwan'，所以 fetch_minute_single 取不到外部分鐘資料、回傳空表（kline_sync.py:855-856）。/minute 端點因此回傳本地的部分資料，並標上 source='local_partial'（kline.py:945-955）。
- 前端完全沒有讀 local_partial：我在 frontend/src 搜尋這個字串，沒有任何結果。資訊列預設顯示最後一根棒的均價（EChartsIntraday.tsx:423、531、591-594）。
- 價格線和均價線都設了 connectNulls: true（EChartsIntraday.tsx:364、375；EChartsMultiDayIntraday.tsx:222、269），盤中 110 分鐘的空檔會被直線接起來，畫面上看不出來。所以「畫面沒有任何標示」成立。

二、正確的定義（我找的來源跟 finding 不同）
- TradingView 的 VWAP 說明寫著「The indicator begins calculating at the open and stops calculating at the close」，而且預設每個 Session 重新起算。
- 中文維基的定義是「特定时间范围内交易价值与交易总数量的比率」。
- 所以畫面標成「均價」的值，應該涵蓋整個交易時段。

三、本地重算
- 指令：uv run python verify-G5-avg/vwap_check.py。直接讀 kline_minute 的 parquet，跟 reference 的 twse_stock_day 比對。
- 共 36 個「個股×交易日」組合，全部重現：
  - 最大偏差是 +100.4bp，出在 2454 的 9/22：5377.20 對官方 5323.73。
  - 9/23 的三檔：2454 為 5204.78 對 5192.23（+24.2bp）；2379 為 769.26 對 772.08（-36.5bp）；6196 為 547.36 對 546.77（+10.7bp）。
  - 分鐘量占官方日量的比例：9/23 為 0.211～0.547；4 天合計為 0.120～0.866。
  - 9/23 的分鐘棒只到台北時間 11:38，下一根直接跳到 13:28（空了 110 分鐘），而且那根量為 0。第一根 09:32 的量也是 0，是重啟後的基準。
- 自選的 5 檔個股 4 個錄到的交易日都有偏差，所以在使用者情境下確實會發生。

四、嚴重度
- 這個值只用在畫面顯示，沒有被訊號或策略引用。後端唯一的 vwap_bias 在 scoring.py:196-198，用的是日線資料。
- 程式在模組說明裡也承認只能涵蓋有在錄製的時段。
- 核心問題是標示不實、數值偏離真正的定義，屬於「明確功能錯誤」，P2 合理，不到 P1。

五、finding 沒提到的新面向（可能跟 A13 部分重疊，我無法確認 A13 的內容）
即使錄製程式整天沒中斷，均價也永遠不會等於全日均價，原因有兩個：
(a) 開盤集合競價的量永遠不計：開盤前的快照在 :124 就被濾掉，不會碰到累計量。所以當天第一筆盤中快照一定只當基準，09:00 撮合的量加上第一次輪詢之前的成交，全部歸零。這點是從程式碼推出來的；現有 4 天都不是從開盤前開始錄，資料裡看不到實例。
(b) 收盤集合競價的量永遠不計：
- 中文維基寫明台股 13:25～13:30 是收盤集合競價。
- record() 用本機時間判斷盤別，只有 t <= 13:30:00 才算 OPEN（calendar.py:118）。close_final 階段則是 t > 13:30:00 才開始輪詢（quote_service.py:1224-1225、602-610），只 flush 還沒寫出的那根棒。所以 13:30 撮合的那筆快照會被丟掉。
- 資料佐證：4 天都沒有 13:29 或 13:30 的分鐘棒。9/18 錄到 13:28，那根量為 0。
因此 finding 建議的「涵蓋率門檻」即使在全程錄製的日子也會偏低；真正要修，得用官方累計成交金額和成交量來錨定，或者把這個值改標為觀測區間均價。
    - 證據：
      - frontend/src/lib/intraday-chart.ts:16-26：公式是累計 amount ÷ 累計 volume，本身正確
      - backend/app/taiwan/realtime/minute_recorder.py:81（非 OPEN 就 return 0）、:124（09:00～13:30 以外的快照直接丟掉）、:127-130（換日時清空累計量）、:134-142（previous 為 None 時增量為 0）、:150、:161（amount = price × delta）
      - backend/app/api/kline.py:945-955：本地資料不完整、又取不到即時資料時，回傳 source='local_partial'；在 frontend/src 搜尋 local_partial，沒有任何結果
      - backend/app/services/kline_sync.py:855-856：provider 為 'taiwan' 時回傳空表；data\user_data\preferences.json 的 minute_data_provider 是 'taiwan'
      - frontend/src/components/EChartsIntraday.tsx:364、375（connectNulls: true，盤中空檔被直線接起來）、:368（series 名稱 '均價'）、:421、423、531、591-594（預設顯示最後一根棒的均價）
      - frontend/src/components/EChartsMultiDayIntraday.tsx:66、222、265、269、418（每日均價同樣只用錄到的分鐘棒，也 connectNulls）
      - backend/app/taiwan/realtime/calendar.py:118（t <= 13:30:00 才算 OPEN）；backend/app/services/quote_service.py:1224-1225（t > 13:30 進入 close_final）、:602-610（close_final 抓完行情只做 flush）
      - https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/：『The indicator begins calculating at the open and stops calculating at the close.』，Anchor Period 預設為 Session
      - https://zh.wikipedia.org/wiki/成交量加權平均價：『特定时间范围内交易价值与交易总数量的比率』
      - https://zh.wikipedia.org/wiki/臺灣證券交易所：收盤 13:25～13:30 為集合競價，開盤 8:30～9:00 為集合競價
      - 本地計算：cd backend && uv run python scratchpad\audit\work\verify-G5-avg\vwap_check.py → 36 個組合；max_abs_bp=+100.43（2454.TWSE 2026-09-22，5377.20 對官方 5323.73，涵蓋率 0.222）；9/23：2454 為 5204.78 對 5192.23（+24.2bp）、2379 為 769.26 對 772.08（-36.5bp）、6196 為 547.36 對 546.77（+10.7bp）
      - 本地計算：9/23 的 2454 第一根 09:32（UTC 01:32）量為 0；03:38 之後直接跳到 05:28（台北 11:38 → 13:28，空 110 分鐘），該根量為 0
      - 本地計算：4 天中台北時間 13:25 以後只有 9/18 的 13:25～13:28 和 9/23 的 13:27～13:28 有棒，沒有任何 13:29 或 13:30 的棒；13:27／13:28 的量都是 0
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：computeIntradayAverage（intraday-chart.ts:16-26）的公式 Σamount/Σvolume 是正確的，但輸入的本地分鐘棒只涵蓋錄製有在跑的時段。實測有三種缺口：(1) 啟動或重啟後的第一筆快照增量為 0（minute_recorder.py:134-142）；9/23 在 13:27:49 由 watchfiles 重啟後，13:27/13:28 那根量為 0，11:40～13:27 之間完全沒有記錄。(2) 報價中斷但行程沒有重啟時，空檔內的量會全部併到下一根，並以該根價格計價；例如 9/18 2454 的 12:50 那根有 1,246,000 股，約為中位數的 71 倍、占當日量 12%。(3) 開盤到啟動之間、停錄期間都缺資料。結果是 36 個「個股×交易日」組合的分鐘量只占官方日量的 12.0%～86.6%，收盤時的畫面均價和官方全日均價（STOCK_DAY 的成交金額／成交股數）相比，|偏差| 中位數 10.8bp、平均 19.5bp；≥20bp 的有 13 組，最大 +100.4bp（2454 在 9/22：5377.20 對 5323.73）。API 雖然回傳 source='local_partial'，前端沒有處理，單日和多日分時圖仍都標示為『均價』。典型偏差約 0.1%，最多約 1%，並不是普遍落在 0.2%～1%。6196 在 9/23 對官方的偏差是 +10.7bp，不是對 Yahoo 算出的 +24.9bp。
    - 理由：我自己重算的數字和 finding 的每個數值都對得上。做法是用 Python 移植 computeIntradayAverage（Σamount/Σvolume，volume>0 時才除，否則用 close），直接讀 data/kline_minute 下 4 個分區（9/17、9/18、9/22、9/23，每天 9 檔，共 36 組），再和官方參考資料 twse_stock_day_202608_202609.csv 的 amount_twd/volume_shares 比對。
(1) 收盤時的畫面均價：2454 在 9/23 為 5204.78（官方 5192.23，+24.18bp；以 Yahoo 的 5192 算是 +24.62bp，和 finding 的 +24.6 相同）；2379 在 9/23 為 769.26（官方 772.08，-36.46bp；以 Yahoo 算 -35.45bp，和 finding 的 -35.5 相同）；6196 在 9/23 為 547.36（以 Yahoo 算 +24.88bp，和 finding 相同，但對官方 546.77 只有 +10.74bp）。
(2) 最大偏差是 2454 在 9/22：5377.2006 對 5323.7337，+100.43bp，和 finding 一致。那天最後一根分鐘棒在 11:34，也和 finding 一致。
(3) 分鐘量合計占官方日量的比例：9/23 為 0.2111～0.5474，36 組全部為 0.1200～0.8663，都和 finding 一致。
(4) 9/23 的分鐘棒從 09:32/09:33 到 11:38，接著跳 109～110 分鐘到 13:27/13:28，只剩一根，而且量為 0，和 finding 一致。backend.log 顯示 11:40:02 到 13:27:40 沒有任何日誌，13:27:41 shutdown，13:27:49 watchfiles 重啟後，行情服務又開始輪詢。所以 13:28 那根確實是重啟後的基準棒。
另外找到機制 (2) 的實例：9/18 在 12:04 到 12:50 之間報價抓取失敗、行程沒有重啟，12:50 那根 2454 的量是 1,246,000 股（當日中位數 17,500 股，約 71 倍），金額 5.8936e9 正好等於 4730×1,246,000，也就是 46 分鐘的量全部以該根價格計價，約占當日官方量的 12%。
程式碼也逐一核對過：minute_recorder.py:65-68、134-142 讓第一筆快照的增量為 0；:141、:150、:161 的 amount 等於價格×Δ量，不管中間隔了多久；:81 在非 OPEN 時不記錄。API 在本地資料不完整、而且沒有即時來源時，/minute 會回傳 source='local_partial'（kline.py:955-965），但前端完全沒有處理 local_partial（grep 零命中），EChartsIntraday.tsx:368 和 :591-593、EChartsMultiDayIntraday.tsx:265 和 :418 都照樣顯示為『均價』。docs 裡查不到這個問題的既有記錄。
小修正有兩點：(a) 36 組裡 |偏差| 的中位數是 10.75bp、平均 19.5bp；≥20bp 的有 13 組，≥50bp 的只有 3 組，<10bp 的有 17 組。所以 user_impact 寫的『偏高或偏低 0.2%～1%』是偏上緣的說法，典型偏差是 0.1% 左右，最多約 1%。(b) 6196 的 +24.9bp 是拿 Yahoo 的 546.00 比，但 Yahoo 這個值本身和官方 546.77 就差了 -14bp，對官方的偏差只有 +10.7bp。這兩點不影響 finding 成立，嚴重度維持 P2。
    - 證據：
      - 重算腳本與輸出：AUDIT_SCRATCH/work/verify-vwap/recompute.py → recompute_out.csv；執行指令為 cd backend && uv run python recompute.py。
      - 並列比較（finding → 重算）：2454 9/23 5204.78 → 5204.7821（官方 5192.2292，+24.18bp；以 Yahoo 5192 算 +24.62bp）；2379 9/23 769.26 → 769.2631（官方 772.0779，-36.46bp；以 Yahoo 算 -35.45bp）；6196 9/23 547.36 → 547.3584（官方 546.7709，+10.74bp；以 Yahoo 算 +24.88bp）；2454 9/22 5377.20 對 5323.73 +100.4bp → 5377.2006 對 5323.7337 +100.43bp。
      - 涵蓋率（Σ分鐘量／官方 volume_shares）：9/23 為 0.2111～0.5474；36 組全部為 0.1200（2379 在 9/17：372,000／3,100,032）～0.8663（00885 在 9/18）。
      - 偏差分布：36 組 |dev| 最大 100.43bp、中位數 10.75bp、平均 19.52bp；≥20bp 的 13 組，≥50bp 的 3 組（2454 9/22 100.4、6196 9/22 97.3、3034 9/22 57.1），<10bp 的 17 組。
      - 9/23 分鐘棒：9 檔都是 09:32/33 開始，最大空檔為 11:38→13:27/13:28（109～110 分鐘），最後一根量為 0；9/22 的 9 檔都在 11:31/11:34 結束。
      - data/backend.log：9/23 11:40:02 之後沒有任何日誌，直到 13:27:40；13:27:41 出現 'app.main: shutdown'，13:27:49 出現 'watchfiles.main: 7 changes detected'、'starting (mode=official)'、'行情服务已启动'，可見 13:28 那根是重啟後的基準棒。
      - 機制 (2) 的實例（gap_bars.py 輸出）：9/18 的 2454 在 12:04 之後跳到 12:50，該根 volume=1,246,000、amount=5.8936e9（=4730×1,246,000），當日分鐘量中位數為 17,500；backend.log.1 在 9/18 12:04:08 記錄 MIS 讀取逾時、DNS 失敗。
      - backend/app/taiwan/realtime/minute_recorder.py:134-142：previous 為 None 時 delta=0，只寫入基準。:141、:150、:161 的 amount = price×delta，不看經過了多久。:81 在非 OPEN 時直接 return 0。
      - frontend/src/lib/intraday-chart.ts:16-26：公式為 Σamount/Σvolume，本身正確。
      - backend/app/api/kline.py:955-965：資料不完整時回傳 source='local_partial'；前端 src 內 grep 'local_partial' 零命中，StockIntradayChart.tsx 只處理 source==='none'（:56）。
      - frontend/src/components/EChartsIntraday.tsx:368（series name '均價'）、:421、:591-593（資訊列顯示 avg）；EChartsMultiDayIntraday.tsx:66、:265（`${date} 均價`）、:418。

### [P2 → P2] 分時圖金額格式化把 10億 當成『1億』，10億以上的成交額顯示小 10 倍（確認成立）

- **主張**：fmtAmt 與 formatAmount 在 v ≥ 1_000_000_000 時除以 1e9 並加上『億』，但 1 億 = 1e8。所以 ≥10億 的金額少一個數量級；1億～10億 之間則以『萬』顯示（例如『20807萬』），數值沒錯，但沒有換成億。
- **正確的定義或數值**：≥1e8 時以 v/1e8 顯示『億』。
- **app 實際行為**：門檻與除數都用 1e9。
- **對使用者的影響**：聯發科這類高價股，在開盤或空檔後合併的分鐘棒，懸停時『額』少顯示 10 倍（4 天內出現 4 次）。
- **修法建議**：兩處都改成 `if (v >= 1e8) return (v / 1e8).toFixed(2) + '億'`，並抽成共用的格式化函式，避免兩份複本分別修改。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/EChartsIntraday.tsx:33-37（使用處 :598）
  - frontend/src/components/EChartsMultiDayIntraday.tsx:32-36（使用處 :420）
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/億：『數值相當於100000000（10⁸，一萬萬）』
- **本地計算證據**：
  - node amt_harness.ts（直接執行原碼）：3744 根分鐘棒中，≥1億 的有 170 根，≥10億 的有 4 根。
  - 2454 在 9/18 12:50，amount 5,893,580,000 → 顯示『5.89億』，應為 58.94億；0050 在 9/18 12:50，1,665,336,600 → 『1.67億』，應為 16.65億；2454 在 9/18 11:35，1,230,925,000 → 『1.23億』，應為 12.31億。
  - 0050 在 9/18 11:08，208,067,400 → 『20807萬』。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：EChartsIntraday.tsx:33-37 的 fmtAmt 與 EChartsMultiDayIntraday.tsx:32-36 的 formatAmount，在 v ≥ 1e9 時以 v/1e9 加上『億』顯示。但依教育部辭典，億為『萬的萬倍』，即 1e8，所以 ≥10億 的分時懸停『額』會少顯示 10 倍。例如 2454 在 9/18 12:50 成交額 58.94億，被顯示成『5.89億』；本機 4 天資料中共 4 根棒受影響，另一根是 finding 沒列出的 9/22 10:24。1億到10億之間則顯示成『xxxxx萬』，數值正確，但與專案 lib/format.ts 用 1e8 當作億的慣例不一致。建議兩處都改用 lib/format.ts 的 fmtBigNum，或改成 ≥1e8 時除以 1e8。
    - 理由：結論：finding 成立，P2 恰當。

1. 程式碼：兩個格式化函式的行號與 finding 一致。它們在 v ≥ 1e9 時都以 v/1e9 加上「億」顯示，所以 ≥ 1e9 的金額會少一個數量級（1e9 被顯示成「1.00億」，實際是 10億）。1e8 到 1e9 之間則落到「萬」分支，例如「20807萬」。這個數值本身沒錯，但沒有換成億。

2. 單位：分鐘棒的 amount 確實是新台幣元。minute_recorder 用 amount = price * delta 計算（delta 是成交量增量），而 mis_provider 已經把張乘以 1000 換算成股。前端 intraday-chart.ts:13 的註解也寫明「amount in TWD」。所以問題不在單位，只在格式化。

3. 定義：用了兩個與 finding 不同的來源。教育部《重編國語辭典修訂本》的「億」條目寫「數目字。萬的萬倍。」，也就是 1e8；英文 Wikipedia〈Chinese numerals〉列出 萬=10^4、億=10^8。另外，專案自己的 frontend/src/lib/format.ts（fmtVolume、fmtBigNum、formatNumber）都用 1e8 當作億，只有這兩個元件寫錯。

4. 實際資料與重跑：以 polars 唯讀讀取本機 kline_minute 共 4 天、3744 根分鐘棒，涵蓋使用者自選的全部 9 檔。其中 ≥ 1e8 的有 170 根，≥ 1e9 的有 4 根，數字與 finding 相同。把原始碼抽出來用 node 執行，finding 列的顯示值全部重現：2454 在 9/18 12:50 顯示「5.89億」，應為 58.94億；0050 在 9/18 12:50 顯示「1.67億」，應為 16.65億；2454 在 9/18 11:35 顯示「1.23億」，應為 12.31億。另外還有一根 finding 沒列出：2454 在 9/22 10:24 顯示「1.09億」，應為 10.94億。

5. 使用者情境：錯誤只出現在滑鼠懸停時的資訊列，不影響任何計算，但顯示的數值確實錯了 10 倍。EChartsIntraday 經由 StockIntradayChart 在 StockPanel 中使用；EChartsMultiDayIntraday 經由 StockMultiDayIntradayChart 在 StockPreviewDialog 中使用。以使用者的自選股來看，會確實發生：2454 單價約 4700 元，一分鐘成交額常在 1e8 以上（中位數 6773 萬，4 天內有 145 根 ≥ 1e8）；≥ 1e9 的 4 根中，至少 2454 在 9/18 12:50 那根，是 12:04 之後的空檔累積量併進同一根棒。

6. 嚴重度：顯示值差 10 倍屬於明確的功能錯誤，定為 P2 合理。1e8 到 1e9 之間顯示成「萬」只是呈現不一致，這部分單獨看約為 P3。
    - 證據：
      - frontend/src/components/EChartsIntraday.tsx:33-37：`if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}億``；使用處在 :598，`{fmtAmt(d.amount)}`
      - frontend/src/components/EChartsMultiDayIntraday.tsx:32-36：formatAmount 的門檻與除數同樣是 1_000_000_000；使用處在 :420，`formatAmount(info.row.amount)`
      - frontend/src/lib/format.ts:16、:30、:45：專案自己的共用格式化都以 1e8／100_000_000 當作「億」，只有上述兩個元件用 1e9
      - backend/app/taiwan/realtime/minute_recorder.py:150、:161：amount = price * delta；backend\app\taiwan\realtime\mis_provider.py:167：volume_shares = v_lots * 1000；frontend\src\lib\intraday-chart.ts:13 註解『amount in TWD』。amount 的單位是新台幣元
      - 線上來源一：https://dict.revised.moe.edu.tw/dictView.jsp?ID=10676（教育部重編國語辭典修訂本）『億』：「數目字。萬的萬倍。」，即 10^8
      - 線上來源二：https://en.wikipedia.org/wiki/Chinese_numerals 的 Large numbers 一節：萬 = 10^4、億 = 10^8（myriad scale，每一級是前一級的 10,000 倍）
      - 本地計算（polars 唯讀讀取 data/kline_minute 4 個分區）：總計 3744 根；≥1e8 有 170 根（2454 有 145 根、0050 有 13 根、3034 有 4 根、2379 有 3 根、6196 有 1 根）；≥1e9 有 4 根，分別是 2454 9/18 12:50 amount=5,893,580,000、0050 9/18 12:50 1,665,336,600、2454 9/18 11:35 1,230,925,000、2454 9/22 10:24 1,094,170,000
      - node 執行從原始碼抽出的兩個函式：5893580000→『5.89億』（正確為 58.94億）；1665336600→『1.67億』（16.65億）；1230925000→『1.23億』（12.31億）；1094170000→『1.09億』（10.94億）；208067400→『20807萬』；100000000→『10000萬』。兩個函式的輸出完全相同
      - 2454 9/18 12:50 前一根是 12:04（volume 22,000）；12:50 這根的 volume 是 1,246,000。它把空檔期間的累積量併進同一根棒，與 finding 說的『空檔後合併的分鐘棒』一致
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：EChartsIntraday.tsx 的 fmtAmt（:33-37）與 EChartsMultiDayIntraday.tsx 的 formatAmount（:32-36）都以 1e9 當門檻與除數，卻接上「億」，所以 ≥10 億元的分鐘成交額在懸停資訊列少顯示 10 倍；1 億到 10 億之間則顯示成「萬」（例如 20807萬），數值正確但沒有換成億。本地 3744 根分鐘棒中，≥1 億的有 170 根，≥10 億的有 4 根：2454 9/18 11:35、9/18 12:50、9/22 10:24，以及 0050 9/18 12:50。這 4 根中只有兩根 12:50 棒前面有 46 分鐘空檔，另外兩根是一般分鐘棒，所以問題不限於空檔後合併的分鐘棒。修法：兩處都改用 1e8，或直接改用 lib/format.ts 的 fmtBigNum。
    - 理由：finding 的核心主張完全重現。我從兩個原始檔抽出 fmtAmt／formatAmount 的原文，用 node 直接執行，輸入本地 kline_minute 的全部 3744 根分鐘棒。兩個函式都把門檻與除數設成 1e9，後面接的單位卻是「億」（1 億 = 1e8），所以 ≥10 億的金額少顯示一個數量級；1 億到 10 億之間會退到「萬」分支，例如 999,999,999 顯示成「100000萬」，數值沒錯，但沒有換成億。

我自己算出的數字與 finding 並列如下：
- ≥1e8 的分鐘棒：170 根（finding 也是 170）。
- ≥1e9 的分鐘棒：4 根（finding 也是 4）。
- 2454 9/18 12:50：5,893,580,000 顯示「5.89億」，正確應為 58.94億（與 finding 相同）。
- 0050 9/18 12:50：1,665,336,600 顯示「1.67億」，正確應為 16.65億（與 finding 相同）。
- 2454 9/18 11:35：1,230,925,000 顯示「1.23億」，正確應為 12.31億（與 finding 相同）。
- 0050 9/18 11:08：208,067,400 顯示「20807萬」（與 finding 相同）。
- 第 4 根 finding 沒有列出：2454 9/22 10:24，1,094,170,000 顯示「1.09億」，正確應為 10.94億。

parquet 存的是 UTC 時間，換成台北時間（UTC+8）後，與 finding 的時間一致。

單位交叉驗證：用官方 STOCK_DAY 參考資料（amount_twd，單位元）對照。每檔每日的「分鐘金額合計 ÷ 官方日成交金額」都與「分鐘量合計 ÷ 官方日成交股數」相近。例如 2454 9/18：0.4632 對 0.4625；0050 9/18：0.5996 對 0.5993。所以分鐘棒 amount 的單位確實是元，不是千元，1e9 的除數沒有任何單位理由。

專案內其他格式化函式都用 1e8：lib/format.ts 的 fmtBigNum、EChartsCandlestick、AnalysisKChart。這佐證兩處 1e9 是錯誤。線上來源（維基百科）也確認億 = 10⁸。

需要修正的次要敘述：user_impact 說這些是「開盤或空檔後合併的分鐘棒」，這只對一半。0050 與 2454 的 9/18 12:50 棒前面確實有 46 分鐘的空檔（上一根是 12:04／12:01）。但 2454 9/18 11:35 的前一根是 11:34，9/22 10:24 的前一根是 10:22，都是一般分鐘棒。

嚴重度維持 P2：懸停資訊列的數值錯 10 倍，屬於明確的功能錯誤，但不影響計算、訊號或交易結果。
    - 證據：
      - frontend/src/components/EChartsIntraday.tsx:33-37：`if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}億``；使用處在 :598 `{fmtAmt(d.amount)}`
      - frontend/src/components/EChartsMultiDayIntraday.tsx:32-36：`if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}億``；使用處在 :420 `{formatAmount(info.row.amount)}`
      - 元件確實有在畫面上使用：frontend/src/components/StockIntradayChart.tsx:123、frontend/src/components/StockMultiDayIntradayChart.tsx:187
      - 對照組正確寫法：frontend/src/lib/format.ts:30 `if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(2)}億``；EChartsCandlestick.tsx:101、stock-analysis/AnalysisKChart.tsx:619 都用 1e8
      - 本地執行原碼：`node harness.mjs`（AUDIT_SCRATCH/work/verify-amtfmt/harness.mjs，用 regex 從 .tsx 抽出函式原文後 eval），輸出 `rows=3744 >=1e8=170 >=1e9=4 fmtAmt!=formatAmount=0`
      - 同一輸出的逐筆結果：0050 2026-09-18 12:50 amount=1665336600 → 1.67億（正確 16.65億）；2454 09-18 11:35 1230925000 → 1.23億（正確 12.31億）；2454 09-18 12:50 5893580000 → 5.89億（正確 58.94億）；2454 09-22 10:24 1094170000 → 1.09億（正確 10.94億）；0050 09-18 11:08 208067400 → 20807萬
      - 邊界值：100000000 → 本元件顯示『10000萬』，fmtBigNum 顯示『1.00億』；999999999 → 『100000萬』對『10.00億』；1000000000 → 『1.00億』對『10.00億』；2500000000 → 『2.50億』對『25.00億』
      - 單位驗證（check.py，uv run python）：分鐘棒 amount 合計與官方 twse_stock_day_202608_202609.csv amount_twd 的比值，和成交量比值一致，例如 2454 2026-09-18 amt_ratio=0.463181、vol_ratio=0.46251；0050 2026-09-18 0.599607 對 0.599289；36 個檔日組合都相近，因此 amount 單位為元
      - https://zh.wikipedia.org/zh-tw/億：『數值相當於100000000（10⁸，一萬萬）』
      - 空檔檢查（gaps.mjs）：0050 與 2454 的 09-18 12:50 前一根分別是 12:04、12:02（46～48 分鐘空檔）；2454 09-18 11:35 前一根是 11:34；2454 09-22 10:24 前一根是 10:22

### [P2 → P2] K線量能輸入：日K成交量在 9/16～9/23 比官方 STOCK_DAY 少，VOL5 與量比跟著偏差（可能與日K資料領域重疊）（確認成立）

- **主張**：圖表用的日K成交量（kline_daily_enriched 與 taiwan/daily 相同）在最近幾天有 11/342 列比官方 STOCK_DAY 成交股數少，影響 5 檔個股，ETF 沒有受影響。前端 VOL5、VOL10 與量比的公式本身正確，但結果跟著偏差。
- **正確的定義或數值**：日K成交量和官方 STOCK_DAY 成交股數一致。
- **app 實際行為**：近期部分交易日少計，看起來缺了盤後定價或鉅額交易（推測）。
- **對使用者的影響**：K線成交量柱、VOL5／VOL10、量比標籤在這幾天偏低或偏高，最多達 20%。
- **修法建議**：檢查日K更新流程（工作樹中 backend/app/taiwan/daily_update.py 有未提交修改）的抓取時點與資料集，改在盤後定價與鉅額交易結算後，以 STOCK_DAY_ALL 或 MI_INDEX 的成交股數回補，並回補 9/16～9/23。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/StockDailyKChart.tsx:70（volume 直接來自 API）
  - backend/app/api/kline.py:428-470（讀 enriched 正本）
- **線上證據**：
  - 官方 STOCK_DAY（reference csv/twse_stock_day_202608_202609.csv；manifest 註明統計含一般、零股、盤後定價、鉅額交易，且 9/23 十檔與 MI_INDEX 完全一致）
- **本地計算證據**：
  - python（daily_vs_official.txt）比對 342 列，有 11 處成交量不符，例如：6196 在 9/17 為 808,428，官方 973,428（-17.0%）；2454 在 9/18 為 9,930,705，官方 10,365,174（-4.2%）；2454 在 9/23 為 6,425,491，官方 6,430,491。
  - taiwan/daily 原始分區同值（inspect3.py），quote_ts 為 None。
  - 用官方成交量重算：VOL5 最大偏差 2.69%（6196 在 9/21），量比1 最大 20.2%，量比5／量比20 最大 17.0%。開高低收與官方完全一致。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：日K成交量與成交金額有少計。每日 16:30 的排程（daily_pipeline.py:180）從 MI_INDEX 抓取當天快照；refresh_dates 會略過已落盤的日期（daily_refresh.py:150、167），21:40 那班也不重抓，所以當天快照裡缺少的盤後成交（盤後定價與鉅額交易，屬推測）永遠不會補回。

證據：當天寫入的分區（9/16、9/17、9/18、9/22、9/23）與官方 STOCK_DAY 成交股數相比都有少計；事後才寫入的分區（8/03–9/15、9/21）完全吻合。自選 9 檔中有 11/342 列少計，影響 2379、2454、3034、3545、6196 五檔。自選的四檔 ETF 沒有受影響，但全市場並非 ETF 都不受影響：9/23 有 2 列 00 開頭的標的也少計。

前端 VOL5／VOL10／量比的公式正確，只是結果跟著輸入偏差：VOL5 最大偏差 2.69%，量比1 最大 20.2%，量比5／量比20 最大 17.0%。

影響不只圖表：9/23 全市場有 23/1330 列上市股少計，最嚴重的 6464 少了 99.24%；成交金額同步少計，enriched 的量比、換手率與選股條件也會受影響。

修正方向：日線改在官方資料定稿後再抓（例如隔日早上），或對最近一到兩個交易日做強制重抓與比對，再回補 9/16、9/17、9/18、9/22、9/23 的分區並重算 enriched。

原文的兩處不準確：
- 「daily_update.py 有未提交修改」與成因無關，該檔只有換行符差異。
- 引用行號已偏移，現況為 StockDailyKChart.tsx:72，kline.py:430、465。
    - 理由：這條 finding 成立，數字全部重現，嚴重度維持 P2。原主張有兩點要修正：成因更明確，影響範圍也比原文大。

1) 數字重現：我用 kline_daily_enriched 的 9 檔自選股比對官方 STOCK_DAY 成交股數，342 列中有 11 列不符，全部是少計。受影響的是 2379、2454、3034、3545、6196 五檔，自選的四檔 ETF 沒有不符。開高低收 0 筆不符。VOL5 最大偏差 2.69%（6196 在 9/21），量比1 最大 20.24%（6196 在 9/18），量比5／量比20 最大 16.95%（6196 在 9/17）。這些都與 finding 一致。taiwan/daily 原始分區的值與 enriched 相同（加上 2330 後是 16/380），成交金額也在同樣 16 列少計。

2) 程式碼：前端 toOHLC 直接取 API 回傳的 volume（StockDailyKChart.tsx:72，原引 :70，行號已偏移）。VOL5／VOL10 是含當日的 N 日平均（EChartsCandlestick.tsx:385-392）；量比N 是當日量除以前 N 日平均（EChartsCandlestick.tsx:106-119）。公式本身正確，偏差完全來自輸入。後端 get_daily 在 kline.py:430 讀 enriched，在 :465 轉成 rows；函式範圍是 kline.py:400-471，原引 428-470 大致相符。日K量取自 MI_INDEX 的「成交股數」欄（snapshot_provider.py:164）。

3) 定義：官方 MI_INDEX 與 STOCK_DAY 原始 JSON 的 notes 都寫著「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」，所以 app 的目標口徑是全口徑。另找的兩個非官方來源如下。
- zh.wikipedia〈臺灣證券交易所〉：一般交易 9:00–13:30，「盤後定價、零股及鉅額交易等，則另訂交易時間」。這證明收盤後仍有成交會計入日量。
- Yahoo Finance chart API：2454 的 9/18 量是 9,019,674，9/21 是 9,541,537，比官方與本地都低。Yahoo 用的是另一種較窄的口徑，顯示市面上的日量口徑確實不一，應以交易所全口徑為準。

鉅額交易的確切申報時段，我沒能從非交易所來源查到，因為 WebSearch 額度已用完。

4) 成因比 finding 的「推測」更明確：問題出在抓取時點，而且寫入後不再更新。
- 排程在 16:30 抓 MI_INDEX（daily_pipeline.py:180）。refresh_dates 會略過已落盤的日期（daily_refresh.py:150、167），21:40 那班也明寫「已落盤的日期會被 refresh 略過」（daily_pipeline.py:195）。所以當天 16:30 的不完整快照會永久保留。
- 分區寫入時間把兩類資料完全分開。當天寫入的 5 個分區全部少計：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30。事後才寫入的 33 個分區完全吻合：8/03–9/15 在 9/16 上午補寫，9/21 在 9/22 10:55 才寫入。
- 2454 在 9/23 的量：app 於 16:30:02 抓 MI_INDEX 得到 6,425,491；參考資料於 9/24 01:5x 抓同一端點得到 6,430,491。可見官方資料當晚還被更新過。

少掉的部分有兩種樣態。一種的推算均價剛好等於收盤價，例如 3545 在 9/18 少 6,000 股、推算均價 51.6，符合盤後定價。另一種的推算均價偏離收盤甚至超出當日高低區間，例如 2454 在 9/23 少 5,000 股、推算均價 5018.13，低於當日最低 5140，符合鉅額交易。究竟屬於哪一類，仍然是推測。

5) finding 的不準確處：
- 「daily_update.py 有未提交修改」與成因無關。git diff --numstat 對該檔沒有輸出，只是換行符差異。
- 「ETF 沒有受影響」只適用於自選的四檔 ETF。9/23 全市場比對中，有 2 列代號 00 開頭的標的也少計。
- 影響不只自選股與圖表：9/23 全市場 1330 列上市股中有 23 列少計，最嚴重的 6464 少了 99.24%（本地 2,300 股，官方 302,300 股）。成交金額同步少計，enriched 的量比、換手率與選股條件也會受影響。

6) 嚴重度：就本 finding 的範圍（圖表量能與量比標籤）而言，P2（明確功能錯誤）恰當。資料管線本身每個交易日都會重現，而且造成日K口徑隨抓取時點混用；若在日K資料領域另立一項，可以考慮 P1。在使用者情境下確實會發生：自選股全是上市股，排程每天 16:30 執行，2454 在 5 個當天寫入的交易日全部少計。
    - 證據：
      - 本地比對（cmp.py，輸出在 scratchpad audit\work\verify_g5_vol\cmp_out.txt）：10 檔共 380 列，taiwan/daily 與 kline_daily_enriched 都有 16 列成交量不符，全為少計；成交金額同樣 16 列不符，開高低收 0 筆不符。例：6196 在 9/17 本地 808,428，官方 973,428（-16.95%）；2454 在 9/18 本地 9,930,705，官方 10,365,174；2454 在 9/23 本地 6,425,491，官方 6,430,491。
      - 本地重算（ratio.py）：自選 9 檔共 342 列，11 列不符，受影響的是 2379、2454、3034、3545、6196；VOL5 最大偏差 2.6886%（6196 在 9/21）；量比1 最大 20.238%（6196 在 9/18）；量比5 與量比20 最大 16.950%（6196 在 9/17）。
      - 分區寫入時間：9/16 16:30:22、9/17 16:19:15、9/18 17:03:37、9/22 16:30:03、9/23 16:30:05 都是當天寫入，全部不符；8/03–9/15 於 9/16 11:21 寫入、9/21 於 9/22 10:55 寫入，全部吻合。
      - data/backend.log:2430、37256：9/22 與 9/23 於 16:30 請求 MI_INDEX?date=...&type=ALL；9/23 21:40 那班（backend.log:45470）沒有重抓日線。
      - backend/app/jobs/daily_pipeline.py:180（16:30 排程）、:195（註解：已落盤的日期會被 refresh 略過）、:198（21:40 排程）
      - backend/app/taiwan/daily_refresh.py:150、167（已存在的日期直接略過，不重抓）
      - backend/app/taiwan/providers/snapshot_provider.py:164（量取自 MI_INDEX「成交股數」欄）
      - frontend/src/components/StockDailyKChart.tsx:72（volume: Number(r.volume ?? 0)）；frontend/src/components/EChartsCandlestick.tsx:106-119（量比N＝當日量／前 N 日平均）、:385-392（VOL5／VOL10 含當日）
      - backend/app/api/kline.py:430（repo.get_daily_asset 讀 enriched）、:465（rows = df.to_dicts()）
      - 官方參考 raw/twse_mi_index_20260923.json 每日收盤行情 notes：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」raw/twse_stock_day_2454_202609.json notes：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所：「撮合成交時間為9:00至13:30」；「盤後定價、零股及鉅額交易等，則另訂交易時間、委託及撮合方式」
      - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d（存於 scratchpad verify_g5_vol\yahoo_2454.json）：9/16 5,895,568、9/18 9,019,674、9/21 9,541,537、9/23 5,917,267，都比官方全口徑低，屬較窄的第三方口徑。
      - 少計部分的推算均價（成交金額差除以成交量差）：3034 在 9/17 為 545.0、6196 在 9/17 為 516.0、3545 在 9/18 為 51.6，都等於收盤價；2454 在 9/23 為 5018.13，低於當日最低價 5140，2454 在 9/22 為 5302.8。
      - 9/23 全市場比對（本地分區對官方 MI_INDEX quotes csv）：1330 列上市股中 23 列少計、0 列多計；其中 00 開頭 2 列；最嚴重的 6464 本地 2,300、官方 302,300（-99.24%）。
      - git diff --numstat -- backend/app/taiwan/daily_update.py 沒有輸出，只有 LF→CRLF 警告，內容沒有修改。
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：finding 的數值全部重現。自選 9 檔在 2026-08-03～09-23 共 342 列中，有 11 列日K成交量少於官方 STOCK_DAY，全部落在 9/16～9/23（9/21 除外），分屬 2379、2454、3034、3545、6196 五檔個股；開高低收完全一致。前端 VOL5／VOL10／量比的公式正確，但輸入偏低，所以結果跟著偏差：VOL5 最大 -2.69%，量比1 最大 +20.2%，量比5／量比20 最大 -17.0%。

補充範圍：不在自選的 2330 同樣在 5 天少計，最多 -9.05%，VOL5 偏差 -5.22%。9/23 全市場上市標的有 23/1330 檔少計，最嚴重的 6464 少了 99%；ETF 有 2/240 檔受影響。

成因（有本地證據，交易時段是推測）：daily_update 在當天約 16:20～17:05 抓 MI_INDEX 寫入 taiwan/daily。當時官方數字還沒包含部分後續成交，從缺少部分的反推均價看，比較像是鉅額交易。之後 refresh_dates 對已存在的日期直接跳過，不會重抓，所以早期快照永久保留。隔天才抓的 9/21 和 9/15 以前的日子都正確。

修正方向：延後抓取時間，或隔天用 MI_INDEX／STOCK_DAY_ALL 重抓前一個交易日並覆寫，再回補 9/16～9/23。finding 提到的 daily_update.py 工作樹修改只有換行符差異，與這個問題無關。
    - 理由：我自己寫了腳本，用 polars 直接讀 taiwan/daily 與 kline_daily_enriched 的 2026-08、09 分區，和官方 STOCK_DAY 的 CSV（10 檔，380 列）逐列比對。finding 的每個數字都能完全重現：
- 只看自選 9 檔（不含 2330，9×38=342 列）時，有 11 列成交量少於官方，分屬 5 檔個股：2379、2454、3034、3545、6196。4 檔 ETF 沒有差異。開高低收 0 筆不符。enriched 和 taiwan/daily 的成交量 0 筆不同，quote_ts 全部是 null。
- 例子與 finding 一致：6196 在 9/17 本地 808,428、官方 973,428（-16.95%）；2454 在 9/18 本地 9,930,705、官方 10,365,174（-4.19%）；2454 在 9/23 本地 6,425,491、官方 6,430,491。
- 前端公式照 EChartsCandlestick.tsx 實作：VOLn 是含當日的 n 日均量，量比d 是當日量除以前 d 日均量（不含當日）。重算最大偏差：VOL5 為 -2.69%（6196，9/21）；量比1 為 +20.24%（6196，9/18）；量比5 與量比20 都是 -16.95%（6196，9/17）。與 finding 的 2.69%、20.2%、17.0% 相符。

finding 沒提到的新面向：
(1) 參考資料裡的 2330 雖然不在自選，也同樣少計，9/16、17、18、22、23 共 5 天，9/18 達 -9.05%。它的 VOL5 偏差最大 -5.22%（9/23），比 6196 還大。所以樣本中 6 檔個股全部受影響，ETF 則是 0/4。
(2) 用 MI_INDEX 9/23 對全市場比對：1330 檔上市標的中有 23 檔少計，沒有多計的；最嚴重的 6464 少了 99.2%，1109 少 89.2%，2376 少 33.1%。其中 ETF 類（00 開頭）240 檔中有 2 檔少計，所以「ETF 沒有受影響」只在自選樣本內成立。
(3) 有差異的日子，都是當天 16:19～17:03 抓 MI_INDEX 寫入的：9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30。9/21 是隔天 10:55 才抓，9/15 以前是 9/16 11:21 回補，這些日子都和官方一致。另外 refresh_dates 對已存在的日期直接跳過，所以抓得早的快照之後不會被更正。
(4) 從缺少的成交金額除以缺少的股數，可以反推缺少那部分成交的均價。有些不在當日高低價區間內：2454 在 9/23 反推約 5018，當日最低價 5140。盤後定價交易以收盤價成交，14:30 就結束，因此缺的主要不是盤後定價，比較像是鉅額交易在抓取時還沒併入官方資料（推測）。鉅額交易的時段我沒能上網查到：WebSearch 額度已用完，Wikipedia 沒有寫。
(5) finding 修正建議說「工作樹中 daily_update.py 有未提交修改」，但 git diff 除了 LF/CRLF 警告之外沒有任何內容差異，和這個問題無關。

嚴重度維持 P2。
    - 證據：
      - 本地比對：cd backend && uv run python AUDIT_SCRATCH/work/verify-klvol/compare.py → 10 檔 380 列中成交量不符 16 列，全部在 9/16 以後（60 列中占 16 列）。扣掉 2330 後剩 11 列，涉及 2379、2454、3034、3545、6196；OHLC mismatches: 0；enriched 與 taiwan/daily 成交量差異: 0；tw_qts 全部 null（輸出見 compare_out.txt）
      - 數值並列（本地 vs 官方）：6196 9/17 808,428 vs 973,428（-16.95%，finding -17.0%）；2454 9/18 9,930,705 vs 10,365,174（-4.19%，finding -4.2%）；2454 9/23 6,425,491 vs 6,430,491（finding 相同）
      - finding 未提的 2330：9/16 -0.21%、9/17 -3.30%、9/18 -9.05%（37,190,511 vs 40,892,688）、9/22 -5.08%、9/23 -3.68%
      - 衍生指標重算（derived.py，輸出見 derived_out.txt）。自選 9 檔：VOL5 最大 -2.69%（6196 9/21，1,216,120.2 vs 1,249,720.2）；VOL10 最大 -1.17%；量比1 最大 +20.24%（6196 9/18，2.594 vs 2.157）；量比5 與量比20 最大 -16.95%（6196 9/17）。含 2330 時 VOL5 最大 -5.22%（2330 9/23，22,733,559 vs 23,984,794）
      - 前端公式：frontend/src/components/EChartsCandlestick.tsx:385-390（calcVolMa 取含當日的 n 日平均）；EChartsCandlestick.tsx:106-119（volumeRatioAt = 當日量 / 前 d 日均量）；StockDailyKChart.tsx:70（volume: Number(r.volume ?? 0)）
      - 全市場 9/23（implied.py，輸出見 implied_out.txt）：MI_INDEX 與 taiwan/daily 對上 1330 檔，成交量不符 23 檔，全部少計；00 開頭 240 檔中 2 檔不符；最嚴重 6464 -99.24%、1109 -89.21%、1227 -77.95%、2376 -33.08%
      - 反推缺少成交的均價：2454 9/23 缺 5,000 股、缺金額 25,090,650，反推約 5018.13，當日低價 5140、收盤 5185（落在區間外）；2330 9/22 反推約 2494.79，收盤 2460；6196 9/17 反推 516.0，等於收盤價
      - 抓取時點：data/backend.log:2430（2026-09-22 16:30:00 GET MI_INDEX?date=20260922）、backend.log:37256（09-23 16:30:02）、backend.log.1:48886（09-18 17:03:32）、backend.log.3:64279（09-17 16:19:12）、backend.log.1:53722（9/21 的資料在 09-22 10:55:37 抓取，與官方一致）。分區 mtime：date=2026-09-10～15 都是 2026-09-16 11:21 寫入，與官方一致
      - backend/app/taiwan/daily_refresh.py:167-170：日期已在 store 就 dates_skipped 跳過，不會重抓；daily_refresh.py:146 使用 TWSE MI_INDEX
      - git diff backend/app/taiwan/daily_update.py 只有 'LF will be replaced by CRLF' 警告，沒有內容差異
      - 線上查證鉅額交易時段失敗：WebSearch 額度用盡；https://zh.wikipedia.org/wiki/臺灣證券交易所 只寫到「盤後定價、零股及鉅額交易等，則另訂交易時間、委託及撮合方式。」，沒有時段

### [P3 → P2] 含國外成分的 ETF（00885、00893）沒有漲跌幅限制，分時圖仍畫 ±10% 漲跌停線（確認成立）

- **主張**：/api/kline/minute 的 price_limit.rate 對所有台股一律給 10%，但 app 自己的 MarketProfileBridge 把國外成分 ETF 定為沒有漲跌幅限制。分時圖在『漲跌停』模式用這個 rate 畫線並縮放 Y 軸；自適應模式也用它當上限。
- **正確的定義或數值**：沒有漲跌幅限制的標的不畫漲跌停線，Y 軸也不以 ±10% 為上限。
- **app 實際行為**：畫出並不存在的 ±10% 漲跌停線。
- **對使用者的影響**：自選中 2 檔 ETF 在『漲跌停』模式下看到不存在的漲跌停價；如果單日波動超過 10%，自適應模式的範圍會被截斷。
- **修法建議**：price_limit 改用 MarketProfileBridge.get_price_limit_pct（沒有限制時回傳 null）；前端在 rate 為 null 時隱藏漲跌停按鈕與線，不要退回 10%。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/price_limits.py:55（一律 ORDINARY_TEN_PERCENT）
  - backend/app/api/kline.py:224-236
  - backend/app/taiwan/universe/models.py:131、140-141（國外 ETF 回傳 None）
  - frontend/src/components/EChartsIntraday.tsx:50（rate 不是有限數值時退回 0.10）、:151-181
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/臺灣證券交易所：『每日漲跌幅限制原則上為10%』『但含海外股票的ETF不受此限』
- **本地計算證據**：
  - 官方 ETF 基本資料（reference csv/twse_t187ap47_L_etf_meta.csv）：00885、00893 的 includes_foreign_components 為『是』。
  - 以 9/22 收盤 18.26 計算，00885 的線畫在 20.09／16.43。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：/api/kline/minute 的 price_limit 在台股一律回傳 {rate: 0.1, limit_up: null, limit_down: null, source: 'rule'}。原因是 price_limits.py:55 在沒有 limit_class 時固定用 ORDINARY_TEN_PERCENT，而 kline.py:225-231 從不傳 limit_class；純台股部署的 instruments 目錄是空的，當日權威價也不會覆寫。但 app 的規則正本 MarketProfileBridge（models.py:145-154，scope=='foreign' 時回傳 None）依 security_master 把 00885、00893 判定為沒有漲跌幅限制，官方 ETF 基本資料也把兩檔列為含國外成分（是）。分時圖（EChartsIntraday.tsx:50；rate 非有限數值時也退回 0.10）因此會畫出不存在的漲跌停線：9/23 盤的 00885 為 20.09／16.43（前收 18.26）、00893 為 50.35／41.19（前收 45.77）；今天 9/24 盤分別是 20.10／16.44 與 50.81／41.57。預設的自適應模式會把 Y 軸限制在 ±10%（:177-181）。這兩檔確實出現過超過 10% 的單日波動（00893 在 2025-04-07 收盤跌 17.6%、盤中最低跌 27.6%）；照這個邏輯，該日 Y 軸會被限制在 [19.90, 24.32]，而當天整個價格區間 16.00–18.82 都在軸外，價格線會被裁掉（推測，依 ECharts 預設 clip）。同一根因也讓 00631L 被畫成 10%（應為 20%）、債券 ETF 被畫出不存在的限制。原 finding 引用的 models.py:140-141 是 OFFICIAL_ETF_RULE_PROFILES 路徑（只收 00631L、00632R），00885、00893 實際走的是 :145-154。
    - 理由：我自己跑了腳本，finding 的數值全部重現。

1. 後端給錯漲跌幅
   - `price_limit_pct` 只要遇到台股代號（.TWSE／.TPEX），且呼叫端沒傳 limit_class，就一律回傳 0.10（price_limits.py:55）。
   - kline.py:225-231 呼叫它時從不傳 limit_class。
   - 實測 00885、00893 在 9/23、9/24 兩天都回 0.1。
   - 用 stub repo 呼叫 `_get_price_limit_info`（對應實際部署：instruments、instruments_etf、instruments_index 三個目錄都是 0 個 parquet），回傳 `{'rate': 0.1, 'limit_up': None, 'limit_down': None, 'source': 'rule'}`，當日的權威價覆寫沒有生效。

2. app 自己的規則正本判定為沒有限制
   - security_master.parquet 裡，00885、00893 的欄位是 instrument_type=etf、etf_category=foreign_equity、classification_source=official_metadata、underlying_scope=foreign。
   - 把這兩列餵給 `MarketProfileBridge.get_price_limit_pct`，兩檔都回傳 None；0050、00878、2454 回傳 0.1。

3. 官方與線上資料一致
   - 官方 ETF 基本資料（twse_t187ap47_L_etf_meta.csv）：00885、00893 的 fund_type 都是「國外成分證券指數股票型基金」，includes_foreign_components 為「是」。
   - 維基百科原文：「原則上設有10%的漲跌幅限制，但含海外股票的ETF不受此限。」

4. 數值並列（用 node 執行從 EChartsIntraday.tsx:39-63 抽出的原始 getLimitPrices）

   | 案例 | finding | 我算出的 |
   |---|---|---|
   | 00885，9/23 盤 | 前收 18.26 → 20.09／16.43 | 前收 18.26（本地 kline_daily_enriched 與官方 STOCK_DAY 相同）→ 20.09／16.43，相同 |
   | 00893，9/23 盤 | 未列 | 前收 45.77 → 50.35／41.19 |
   | 00885，今日 9/24 盤 | 未列 | 前收 18.27 → 20.10／16.44 |
   | 00893，今日 9/24 盤 | 未列 | 前收 46.19 → 50.81／41.57 |

   即使後端改成回傳 rate=null，前端的 `Number.isFinite(null)` 為 false，仍會退回 0.10，畫出同樣的 20.09／16.43。這一點與 finding 的修正建議一致。

5. 自適應模式截斷有實例佐證
   - 本地 taiwan/daily 顯示，00893 在 2025-04-07 前收 22.11，當日開 16.00、高 18.82、低 16.00、收 18.21（跌 17.64%）；2025-04-10 漲 13.65%。
   - 00885 在 2025-04-07 跌 11.81%、2025-04-10 漲 11.69%。
   - 另外用 Yahoo chart API 抓 00893.TW 2025-04 前半月的日K，OHLC 數值（22.11 → 開 16.0、高 18.82、低 16.0、收 18.21）與本地相同，證明實際單日跌幅超過 10%。
   - 自適應是預設模式（EChartsIntraday.tsx:419）。在這個模式下 maxDiff 會被壓到 min(實際振幅, 10%)（:177-181）。以 00893 在 2025-04-07 為例，Y 軸會被限制在 [19.90, 24.32]，但當天整個價格區間 16.00–18.82 都落在軸外。
   - 以 ECharts 5 line series 預設 clip=true 推論，這一天的價格線會完全看不到（推測，沒有實際渲染）。
   - 這兩檔的分K是即時輪詢在本地累積的，目前 9/17–9/23 已有資料；今後遇到超過 10% 的日子會發生同樣情況。

6. 嚴重度建議從 P3 調為 P2
   - 「漲跌停」模式下，每一次都會畫出並不存在的漲跌停價。
   - 預設的自適應模式在極端日可能整條價格線都看不到，符合 CONTRIBUTING.md 第 11 節「明確功能錯誤」。
   - 同一根因也影響其他 ETF（不在自選內）：
     - 00631L：bridge 給 0.2，minute API 給 0.1。
     - 00679B（債券 ETF）：bridge 給 None，minute API 給 0.1。
   - 只影響圖表顯示，不影響交易計算，所以不到 P1。

7. 小處更正
   - finding 引用的 models.py:140-141 是 OFFICIAL_ETF_RULE_PROFILES 的路徑，這個表只收 00631L、00632R 兩檔；:131 是 docstring。
   - 00885、00893 實際走的是 models.py:145-154，由 scope == "foreign" 在 :153-154 回傳 None。
   - 此問題與 docs/code-review-2026-09-22.md 的 B5、N5 不重複：那兩項講的是 enriched 的距漲跌停欄位與盤中漲跌停訊號，不是分時圖的 price_limit.rate。

8. 稽核過程沒有寫入 repo：執行前後 git status 相同，都只有原本 3 個未提交修改；執行時設了 PYTHONDONTWRITEBYTECODE=1。
    - 證據：
      - backend/app/price_limits.py:55 `return PriceLimitModel.get_limit_pct(PriceLimitClass.ORDINARY_TEN_PERCENT)`（limit_class 為 None 時，所有台股都走這一行）
      - backend/app/api/kline.py:225-231 呼叫 price_limit_pct 時沒有傳 limit_class；:236-237 只在 trade_date==cn_today() 時嘗試用 instruments 覆寫；:927-929、:941 把 price_limit 放進 /api/kline/minute 的回應
      - backend/app/taiwan/universe/models.py:145-154：scope=='foreign' 時回傳 None（00885、00893 走這條路徑）；:30-39 的 OFFICIAL_ETF_RULE_PROFILES 只有 00631L、00632R
      - frontend/src/components/EChartsIntraday.tsx:50 `Number.isFinite(priceLimit.rate) ? priceLimit.rate : 0.10`；:151-174 漲跌停模式的 Y 軸與 markLine；:177-181 自適應模式 maxDiff=min(maxDiff, limitDiff)；:419 預設 yMode='adaptive'；:289-290 yAxis min/max
      - frontend/src/components/StockIntradayChart.tsx:123-133 沒有傳 showLimitLines（預設 true），priceLimit 取自 minute API
      - 指令：cd backend && PYTHONDONTWRITEBYTECODE=1 uv run python scratchpad\audit\work\verify-g5-fetf\repro.py；輸出存在 AUDIT_SCRATCH/work/verify-g5-fetf/repro_out.txt
      - repro_out 第 2 段：instruments／instruments_etf／instruments_index 的 parquet 檔數都是 0
      - repro_out 第 3-4 段：security_master 中 00885、00893 為 etf/foreign_equity/official_metadata/foreign；MarketProfileBridge.get_price_limit_pct 對兩檔都回 None，0050、00878、2454 回 0.1
      - repro_out 第 5-6 段：price_limit_pct 對 00885、00893 在 2026-09-23、2026-09-24 都回 0.1；_get_price_limit_info 回 {'rate': 0.1, 'limit_up': None, 'limit_down': None, 'source': 'rule'}
      - 官方 reference csv/twse_t187ap47_L_etf_meta.csv：『00885,富邦越南,國外成分證券指數股票型基金,…,是』、『00893,國泰智能電動車,國外成分證券指數股票型基金,…,是』
      - 前收：本地 kline_daily_enriched 的 00885 在 2026-09-22 收 18.26、09-23 收 18.27；00893 在 09-22 收 45.77、09-23 收 46.19；與官方 csv/twse_stock_day_202608_202609.csv 的 close_twd 相同
      - node getLimitPrices.js（原樣抽出 EChartsIntraday.tsx:39-63）：00885 前收 18.26 → limitUp 20.09、limitDown 16.43；rate=null 仍是 20.09／16.43；00893 前收 45.77 → 50.35／41.19；9/24 盤：00885 → 20.10／16.44，00893 → 50.81／41.57
      - 本地 taiwan/daily 日K（2024-01-02～2026-09-23）：00893 在 2025-04-07 前收 22.11、開 16.0、高 18.82、低 16.0、收 18.21（跌 17.64%）；2025-04-10 漲 13.65%。00885 在 2025-04-07 跌 11.81%、2025-04-10 漲 11.69%
      - https://query1.finance.yahoo.com/v8/finance/chart/00893.TW?period1=1743465600&period2=1744416000&interval=1d：前收 22.11 → 開 16.0、高 18.82、低 16.0、收 18.21，接著 17.95 → 20.40，與本地資料相同（WebFetch 的日期換算標錯，但 OHLC 序列相符）
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所 原文：『原則上設有10%的漲跌幅限制，但含海外股票的ETF不受此限。』
      - 同根因延伸（uv run 實測）：00631L.TWSE 的 bridge 回 0.2、price_limit_pct 回 0.1；00679B.TPEX 的 bridge 回 None、price_limit_pct 回 0.1
      - docs/code-review-2026-09-22.md:636（B5）、:659-662（N5）講的是 enriched 距漲跌停欄位與盤中漲跌停訊號，與本 finding 不重複

### [P3 → P3] 分時漲跌停線沒有依升降單位取整（沿用 A 股『四捨五入到分』）（確認成立）

- **主張**：這個部署的 instruments 目錄是空的，所以 price_limit.limit_up／limit_down 永遠是 null，getLimitPrices 一律退回 Math.round(prevClose × 1.1 × 100)/100。台股漲停應依升降單位向下取、跌停向上取（官方資料可證）。
- **正確的定義或數值**：漲停 = floor_tick(參考價 × 1.1)，跌停 = ceil_tick(參考價 × 0.9)，升降單位依價格級距。
- **app 實際行為**：四捨五入到 0.01，會落在不可成交的價位。
- **對使用者的影響**：『漲跌停』模式的參考線偏離最多約 1 個升降單位（2454 差 3 元），屬輕微顯示誤差。
- **修法建議**：後端 /api/kline/minute 直接回傳依升降單位算好的漲跌停價（可重用 MarketProfileBridge.calc_limits，intraday.py:140 已經在用）；前端退回計算也改用台股升降單位表。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/EChartsIntraday.tsx:53-59（註解寫『A股漲跌停價四捨五入到分』）
  - backend/app/api/kline.py:238-251（instruments 缺欄時回傳 null）
- **線上證據**：
  - 官方 MIS 快照（reference csv/mis_snapshot.csv）：2454 y=5180、u=5695、w=4665；6196 y=535、u=588、w=481.5；0050 y=111.85、u=123.0、w=100.7
  - 官方 TWT49U：2379 參考價 704，漲跌停 774／634（704×1.1=774.4 向下取，704×0.9=633.6 向上取）
- **本地計算證據**：
  - node limit_harness.ts：2454 → 5698／4662（官方 5695／4665）；6196 → 588.5／481.5（官方 588／481.5）；0050 → 123.04／100.67（官方 123.0／100.7）；00878 → 38.73／31.69（與官方相同）。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：原敘述正確，數值全部重現。純台股部署下 instruments 目錄是空的，而且查詢日不是今天時 kline.py:236-237 也一律回傳 null，所以分時圖的漲跌停線一律退回 Math.round(prevClose×1.1×100)/100 與 Math.round(prevClose×0.9×100)/100。官方規則是漲停依升降單位向下取、跌停向上取，升降單位依商品類別與價格級距決定。以 9/23 為例：2454 畫成 5698/4662（官方 5695/4665），6196 畫成 588.5/481.5（官方 588/481.5），0050 畫成 123.04/100.67（官方 123.0/100.7），00878 則相同。四捨五入的結果一定落在真正漲跌停價外側，誤差小於 1 個升降單位。

另外補充兩個原 finding 沒提到的面向，建議一併修正：
(A) 00885、00893 屬國外成分 ETF，官方沒有漲跌幅限制（TWT49U 顯示為 9999.95/0.01），但 price_limit_pct 對所有 .TWSE 代號一律回 0.10，圖上畫出了不存在的 ±10% 線。
(B) 除權息當天的 prevClose 取的是未還原的除權息前收盤價，參考線偏差遠大於四捨五入誤差。例如 2379 在 9/8 畫成 801.9/656.1，官方為 774/634。

修法：後端依 MarketProfileBridge 的商品類別計算參考價與漲跌停價後直接回傳；國外成分 ETF 回傳「無漲跌幅限制」，前端就不畫這兩條線。
    - 理由：結論：finding 的數字全部重現，判 confirmed，嚴重度 P3 維持不變。

驗證方法：
1. 用 repo 自帶的 TypeScript 移除型別，直接從 EChartsIntraday.tsx 原樣取出 getLimitPrices 與 isValidPrice 執行（limit_harness.cjs），不是另外重寫一份邏輯。
2. 以官方 MIS 快照的 y、u、w，以及 TWT49U 的除權息參考價與漲跌停價，比對三種算法：前端退回算法、後端 PriceLimitModel.calc_limits_for_pct（依升降單位，漲停向下取、跌停向上取）、官方值。

程式路徑：
- data/instruments、instruments_etf、instruments_index 三個目錄都是空的。
- kline.py:241-249 在 instruments 沒有 symbol 欄時直接回傳 info，limit_up、limit_down 都是 null。
- kline.py:236-237 另有一條：查詢日不是今天時，一律回傳 null。
- 前端因此一律走 EChartsIntraday.tsx:56/59 的 Math.round(x*100)/100，rate 為後端給的 0.10。
- StockPanel.tsx:125-129 的 prevClose 是日 K 前一列收盤價。本地 9/22 收盤價與 MIS 的 y 完全相同（2454 為 5180、6196 為 535、0050 為 111.85、00878 為 35.21），所以 finding 用 y 代入前端算法是正確的。

並列比較（finding 的數字 → 我重算的數字 → 官方值）：
- 2454：5698／4662 → 5698／4662 → 5695／4665
- 6196：588.5／481.5 → 588.5／481.5 → 588／481.5
- 0050：123.04／100.67 → 123.04／100.67 → 123.0／100.7
- 00878：38.73／31.69 → 38.73／31.69 → 相同
- 2379（9/8 參考價 704）：前端算法得 774.4／633.6，官方 774／634

後端依升降單位的算法在上述 4 檔 MIS 資料、7 筆自選股除權息資料上全部與官方一致。擴大到 TWT49U 全部 932 筆有漲跌幅限制的資料列，905 筆一致。其餘 27 筆多為現增、配股等特殊除權，漲停價另有算法，與本 finding 無關。改用「四捨五入到 0.01」只命中 116 筆（這個比例是用 994 列全體算的）。

四捨五入後的值一定不小於依升降單位向下取的值，所以畫出的線永遠在真正漲跌停價的外側，誤差小於 1 個升降單位。自適應模式只把它當上限，不會截掉資料；影響只在使用者手動切到「漲跌停」模式時的參考線位置，P3 合理。

finding 沒提到、這次驗證順帶查到的兩個面向（都有證據，建議另立或併入同一項修正）：
- (A) 國外成分 ETF 沒有漲跌幅限制，前端卻照樣畫 ±10% 線：
  - 00885、00893 在 security_master.parquet 的官方分類都是「國外成分證券指數股票型基金」，underlying_scope=foreign。
  - TWT49U 中 62 筆國外成分 ETF 資料列，官方漲跌停價全是 9999.95／0.01，也就是沒有漲跌幅限制。
  - price_limits.py:79-80 → :55 對所有 .TWSE 代號一律回 0.10，不看商品類別，所以自選股中這 2 檔會被畫出不存在的 ±10% 線（例如 00885 為 20.09／16.43）。
  - repo 自己的 MarketProfileBridge.get_price_limit_pct 對國外成分 ETF 會回 None，只是 kline 這條路徑沒有用它。
- (B) 除權息當天的參考線以除權息前收盤價為基準，偏差遠大於四捨五入誤差：
  - kline_daily_enriched 在 9/7 的 2379 close 與 raw_close 同為 729，是未還原的收盤價。
  - 9/8 前端畫出 801.9／656.1，官方為 774／634。
  - 2454 在 7/7 前端畫出 4537.5／3712.5，官方為 4510／3695。
  - 同一個 prevClose 也會讓當天的漲跌幅以 729 為基準計算（推測，未實際跑畫面）。

網路查證：WebSearch 本 session 額度已用完；兩次嘗試找 Wikipedia 條目，分別為 404 和查無台股內容。因此這條 finding 的佐證完全來自官方參考資料（MIS、TWT49U）。這份資料本身就是交易所公布的漲跌停價，足以支持結論。

唯讀確認：repo 的 git status 前後相同，data 目錄清單的雜湊前後相同。
    - 證據：
      - frontend/src/components/EChartsIntraday.tsx:50-59 — rate 預設 0.10；limit_up／limit_down 無效時用 Math.round(rawUp*100)/100（註解寫『A股漲跌停價四捨五入到分』）
      - backend/app/api/kline.py:236-249 — 查詢日不是今天就直接 return info（null）；instruments 沒有 symbol 欄也 return info
      - data 目錄實況：data/instruments、instruments_etf、instruments_index 皆為空目錄（ls -la 只有 . 與 ..）；config.py:141-144 把 DATA_DIR=./data 解析到專案根目錄
      - frontend/src/components/StockPanel.tsx:125-129 — prevClose 取日 K 前一列 close；本地 2026-09-22 收盤：2454=5180、6196=535、0050=111.85、00878=35.21，與 MIS 的 y 相同
      - 重現指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-g5-tick/verify.py（內部呼叫 node limit_harness.cjs，從 TSX 原樣抽出函式執行）。輸出：2454 frontend=5698/4662、官方=5695/4665；6196 588.5/481.5 vs 588/481.5；0050 123.04/100.67 vs 123.0/100.7；00878 38.73/31.69 vs 38.73/31.69
      - 官方 reference csv/mis_snapshot.csv：2454 y=5180 u=5695 w=4665；6196 y=535 u=588 w=481.5；0050 y=111.85 u=123.0 w=100.7；00878 y=35.21 u=38.73 w=31.69
      - 官方 reference csv/twse_twt49u_20260601_20260923.csv：2379 2026-09-08 ex_ref=704 limit=774/634（前端算法得 774.4/633.6）；2454 2026-07-07 ex_ref=4100.49 limit=4510/3695（前端 4510.54/3690.44）；0050 2026-07-21 ex_ref=98.6 limit=108.45/88.75（前端 108.46/88.74）
      - twt_all.py／twt_nolimit.py：TWT49U 932 筆有漲跌幅限制的資料列，依升降單位的算法（PriceLimitModel.calc_limits_for_pct）有 905 筆與官方一致；四捨五入到 0.01 只有 116 筆一致（以 994 列全體計）
      - 新面向 A：security_master.parquet 中 00885、00893 的 raw_category 為『國外成分證券指數股票型基金』、underlying_scope=foreign；TWT49U 中 62 筆國外成分 ETF 的官方漲跌停價全為 9999.95/0.01（無漲跌幅限制）；backend/app/price_limits.py:79-80、:55 對 .TWSE 一律回 0.10，前端因此畫出 00885 的 20.09/16.43 線
      - 新面向 B：data/kline_daily_enriched/date=2026-09-07 的 2379 close=raw_close=729（未還原）；除權息日前端 prevClose=729 → 801.9/656.1，官方 774/634；2454 在 2026-07-07 為 4537.5/3712.5，官方 4510/3695
      - 唯讀確認：git status --short 前後 diff 為空；data 目錄 ls 的 md5 前後同為 d9481cdd901f5794bd473a65b604e662

### [P3 → P3] 成交量以『股』搭配萬／億顯示，和台股以『張』為單位的慣例不一致；另有『10000萬』的邊界（確認成立）

- **主張**：fmtVol 的輸入是股數（Taiwan volume 以股計），顯示『643萬』之類的值，沒有單位；分時圖的『量』直接顯示股數。台灣看盤慣例以張（1000 股）表示。另外 99,995,000～99,999,999 股會顯示成『10000萬』，而不是『1.00億』。
- **正確的定義或數值**：以張顯示，或明確標示『股』；進位邊界先進位再判斷級距。
- **app 實際行為**：沒有單位的股數萬／億。
- **對使用者的影響**：使用者可能把『643萬』誤讀為 643 萬張（差 1000 倍），屬顯示口徑問題。
- **修法建議**：量能顯示改為股數 ÷ 1000 並加『張』，或至少加上『股』字樣；fmtVol 先四捨五入再判斷是否 ≥1e8。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/EChartsCandlestick.tsx:99-104、183、393-394
  - frontend/src/components/EChartsIntraday.tsx:596
  - frontend/src/components/EChartsMultiDayIntraday.tsx:419
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW：『成交量 5,909 張』『昨量 10,528 張』
- **本地計算證據**：
  - node：fmtVol(6430491)='643萬'（2454 在 9/23，官方 6,430,491 股 = 6,430 張）；fmtVol(99999999)='10000萬'。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：成立，數值完全重現。台股成交量在資料層以股計（契約文件與後端 model 都是股），前端沒有換算。日 K 的「量」、VOL5、VOL10 經 fmtVol 顯示成沒有單位的萬／億股，例如 2454 在 9/23 官方 6,430,491 股（6,430 張）顯示為「643萬」。分時圖與多日分時的「量」直接印出原始股數，例如「41000」，同樣沒有單位。台灣看盤慣例以張為單位，Yahoo 顯示「成交量 5,909 張」。另外，在 TaiwanStockDetail 同一頁上，即時盤面預設以「張」並附單位顯示，旁邊的 K 線副圖卻是沒有單位的萬股，同頁口徑不一致。fmtVol 的進位邊界也成立：整數 99,995,000～99,999,999 股（共 5000 個值）會顯示「10000萬」而不是「1.00億」，但實際上非常罕見，本地 1,423,597 列中只有 1 列，自選股近兩個月官方資料中沒有。嚴重度維持 P3。範圍外另有一個更嚴重、應另開 finding 的問題：分時圖的金額格式化（EChartsIntraday.tsx:33-37、EChartsMultiDayIntraday.tsx:32-36）在 ≥1e9 時除以 1e9 卻標「億」，顯示值少 10 倍。
    - 理由：我用重新計算的方式確認了這條 finding。fmtVol 的程式碼是用 node 從原始檔抽出後直接 eval 執行，沒有手動轉抄。

1. 單位：資料層的成交量是「股」。依據有三：契約文件寫明 K 線用 share volume；後端 model 的欄位說明是「成交量 (股)」；本地 parquet 中 2454 在 9/23 的日量是 6,425,491，官方 STOCK_DAY 是 6,430,491 股。分鐘 K 的 volume 都是 1000 的倍數，而且 amount/volume 等於股價，所以也是股。
2. 資料流：前端沒有換算，原值直接進圖表。TaiwanStockDetail 的 ohlcRows 和 StockDailyKChart 都把 r.volume 原樣傳給 EChartsCandlestick。
3. 顯示：日 K 的「量」、VOL5、VOL10 都經過 fmtVol，輸出沒有單位。分時圖和多日分時的「量」直接用 toFixed(0) 印出股數，也沒有單位。
4. 數值重現：fmtVol(6430491)='643萬'，與 finding 一致；本地值 6425491 同樣得到 '643萬'。對應的張數是 6,430 張。
5. 邊界重現：逐一掃過整數，[99,995,000, 99,999,999] 共 5000 個值都輸出 '10000萬'，1e8 則輸出 '1.00億'，與 finding 完全相同。這個邊界真的會發生但非常罕見：本地 enriched 資料共 1,423,597 列，只有 1 列落在區間內（2609.TWSE 2024-11-27，99,998,121 股）；官方 8～9 月自選資料 380 列中是 0 列。不過 0050 與 00878 的日量最高都超過 1e8，所以「億」這個級距在使用者的自選股上確實會用到。
6. 慣例：Yahoo 2454 頁面顯示「成交量 5,909 張」「昨量 10,528 張」，與 finding 相同。
7. 新面向：同一個 TaiwanStockDetail 頁面裡，即時盤面的成交量預設以張顯示並附單位，旁邊的 K 線副圖卻顯示沒有單位的「643萬」股。同頁口徑不一致，加重了誤讀的可能。

嚴重度維持 P3。資料層沒有錯，只是顯示時缺單位，而且與慣例不同，屬於局部體驗問題。

範圍外的旁證（不屬於本 finding）：EChartsIntraday 的 fmtAmt 和 EChartsMultiDayIntraday 的 formatAmount 在 v ≥ 1e9 時除以 1e9 卻標「億」（1億 = 1e8），金額會少 10 倍。本地分鐘資料中確實有例子，建議另開 finding（P2，明確的功能錯誤）。此外，本地 2454 在 9/23 的日量比官方少 5,000 股，原因未查（推測與盤後定價有關），也不屬於本 finding。
    - 證據：
      - frontend/src/components/EChartsCandlestick.tsx:99-104：fmtVol 在 v>=1e8 時為 (v/1e8).toFixed(2)+'億'，在 v>=1e4 時為 (v/1e4).toFixed(0)+'萬'，沒有單位字樣
      - frontend/src/components/EChartsCandlestick.tsx:183（『量』）與 :393-394（VOL5/VOL10）都呼叫 fmtVol
      - frontend/src/components/EChartsIntraday.tsx:595-596 與 frontend/src/components/EChartsMultiDayIntraday.tsx:419：『量』直接以 volume.toFixed(0) 顯示，沒有單位
      - frontend/src/pages/TaiwanStockDetail.tsx:158-167：ohlcRows 以 volume: r.volume 原值傳給 :544 的 EChartsCandlestick；同頁 :78 volUnit 預設 'lots'，:176-183 的 formatVol 會附『張』／『股』，因此同頁口徑不一致
      - frontend/src/components/StockDailyKChart.tsx:72：volume: Number(r.volume ?? 0)，沒有換算
      - backend/app/taiwan/detail_models.py:79：volume 欄位說明為『成交量 (股)』；docs/taiwan-market-contract-v1.md:19：『K-line fields are timestamp, OHLC and share volume』
      - 官方參考 csv/twse_stock_day_202608_202609.csv：2454, 2026-09-23, volume_shares=6430491（raw/twse_stock_day_2454_202609.json 為 '6,430,491'）
      - 本地 parquet data\taiwan\daily\date=2026-09-23：2454.TWSE volume=6,425,491；data\kline_minute\date=2026-09-23 的 2454 分鐘列如 volume=41000、amount=2.1197e8（每股 5170 元，確認單位是股）
      - node 腳本 verify-fmtvol\fmtvol_repro.js（從 tsx 抽出 fmtVol 原文後 eval）的輸出：fmtVol(6430491)='643萬'、fmtVol(6425491)='643萬'、fmtVol(99994999)='9999萬'、fmtVol(99995000)='10000萬'、fmtVol(99999999)='10000萬'、fmtVol(1e8)='1.00億'；逐一掃整數得到的區間為 99995000..99999999，共 5000 個
      - 與 finding 並列：finding 說 fmtVol(6430491)='643萬'，重現值 '643萬'，一致；finding 說 fmtVol(99999999)='10000萬'，重現值 '10000萬'，一致；finding 說邊界區間為 99,995,000～99,999,999，重現值 99995000..99999999，一致
      - verify-fmtvol\exposure.py：官方 9/23 的 2454 為 6,430 張；VOL5=9,332,027 股（顯示 '933萬'）；0050 與 00878 在 8～9 月的最高日量為 169,497,151 與 168,685,898 股（會進入『億』級距）；官方 380 列中落在『10000萬』區間的為 0 列；本地 enriched 1,423,597 列中為 1 列（2609.TWSE 2024-11-27，volume=99,998,121）
      - https://tw.stock.yahoo.com/quote/2454.TW（WebFetch）：『成交量 5,909 張』『昨量 10,528 張』『成交金額 306.82 億』，證實看盤慣例以張計量、1億=1e8
      - 範圍外旁證：EChartsIntraday.tsx:33-37 的 fmtAmt 與 EChartsMultiDayIntraday.tsx:32-36 的 formatAmount 在 v>=1e9 時為 (v/1e9).toFixed(2)+'億'，金額少 10 倍。verify-fmtvol\minute_amt.py：自選股分鐘列中 4 列 amount>=1e9，例如 0050.TWSE 2026-09-18 04:50Z amount=1,665,336,600 會顯示『1.67億』（正確為 16.65億），2454.TWSE 2026-09-18 03:35Z 1,230,925,000 會顯示『1.23億』（正確為 12.31億）；另有 166 列 1e8≤amount<1e9 顯示為『10000萬』以上的格式。docs/code-review-2026-09-22.md 沒有記錄此問題

### [P3 → P3] 迷你分時的均線用收盤價簡單平均、橫軸按資料點等距，和個股分時圖的 VWAP 與時間軸不一致（確認成立）

- **主張**：MiniIntraday 的均線是收盤價累計算術平均（原碼註解自認是近似），而 rows 其實已經帶 amount 與 volume；橫軸用 i/(n-1) 等距，分鐘棒有缺口或盤中時間尚早時，時間被壓縮或拉伸。
- **正確的定義或數值**：迷你分時直接用 computeIntradayAverage，橫軸對齊 FULL_DAY_TIMES。
- **app 實際行為**：均線口徑不同、時間軸失真。
- **對使用者的影響**：自選表迷你分時的均線位置和點開後的個股分時不同，相差可達 0.9%。
- **修法建議**：改用 computeIntradayAverage(rows)，並以 formatMinuteTime 對應到 271 格時間軸計算 x。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/stock-table/MiniIntraday.tsx:7、62-74（close 簡單平均）、:81（xScale 以索引計）
  - frontend/src/lib/intraday-chart.ts:16-26（個股圖用 VWAP）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/6196.TW：『均價 546.00』
- **本地計算證據**：
  - vwap_compare.txt：6196 在 9/23 迷你均線 542.45、個股圖 VWAP 547.36、Yahoo 546（迷你均線 -0.65%）；36 個『個股×交易日』組合中，迷你均線對官方均價最大偏差 -79.1bp（6196 在 9/23），個股圖同一天是 +10.7bp。
  - 9/23 有 84 根棒，分布在 236 分鐘內，其中 110 分鐘是缺口，但畫成等距。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：MiniIntraday（MiniIntraday.tsx:62-74）的均線是收盤價的累計算術平均，橫軸（:81）按資料點等距排列。個股分時圖則用 computeIntradayAverage（累計成交金額除以累計成交量），並把每根棒對到 FULL_DAY_TIMES 的 271 格時間軸。兩個畫面讀的是同一份本地分鐘 K，但均線口徑和時間軸都不一致。

以 6196 在 9/23 為例，迷你均線 542.45、個股圖 VWAP 547.36，兩者相差 -89.8bp；對官方均價 546.77（STOCK_DAY 的成交金額除以成交股數），迷你偏 -79.1bp、VWAP 偏 +10.7bp。對 Yahoo 的『均價 546』，迷你偏 -0.65%。在 36 組個股×交易日中，迷你均線和 VWAP 的收盤差最大約 0.9%，盤中逐點最大約 1.1%（6196 在 9/22）。

時間軸部分，6196 在 9/23 只有 83 根棒（不是 84 根），09:32 到 13:28 的 237 個分鐘格缺了 154 格，其中 11:38 直接跳到 13:28 是最大的一段缺口。等距畫法把 11:38 那根棒放在寬度 98.8% 的位置，照時間應在 58.5%。

補充：個股圖的 VWAP 同樣只用本地錄到的一部分分鐘棒計算，涵蓋官方成交量的 12% 到 87%，所以本身也可能明顯偏離官方均價，例如 2454 在 9/22 偏 +100bp；36 組中有 16 組是迷你均線更接近官方。這條問題的本質是兩個畫面彼此不一致，不能說成個股圖準確、迷你圖錯誤。修正方式仍建議迷你圖改用 computeIntradayAverage，並依 formatMinuteTime 把 x 座標對到 271 格時間軸。
    - 理由：我用本地分鐘 K parquet（4 個分區，9 檔 × 4 天，共 36 組）加上官方 STOCK_DAY 參考資料（均價取成交金額除以成交股數）重新計算。finding 的核心數字都一模一樣重現：6196 在 9/23 的迷你均線是 542.4458，個股圖 VWAP 是 547.3584，官方均價 546.7709；迷你均線對官方偏 -79.1bp，VWAP 對官方偏 +10.7bp。36 組當中，迷你均線對官方的最大偏差就是這一組。迷你均線和個股圖 VWAP 的收盤差距是 -89.8bp，也就是 finding 說的「可達 0.9%」。Yahoo 頁面（資料時間 2026/09/23 14:30）顯示「均價 546」，迷你均線相對它是 -0.651%，和 finding 的 -0.65% 相符。

程式碼方面，MiniIntraday 的註解自承用收盤價的累計平均來近似，第 62-74 行的實作也確實如此；第 81 行的 x 座標是 i/(n-1)，也就是按資料點等距排列。個股圖（EChartsIntraday.tsx:421）用的是 computeIntradayAverage（intraday-chart.ts:16-26，累計成交金額除以累計成交量），並透過 FULL_DAY_TIMES 把每根棒放到 271 格時間軸上（EChartsIntraday.tsx:67-80）。兩個畫面讀的是同一份本地 kline_minute：minute-batch 和 minute 端點都走 repo.get_minute*。instruments_etf 目錄目前是空的，所以 4 檔 ETF 也被當成個股讀取 kline_minute，因此 36 組都能拿來比較，比較有效。rows 型別本來就有 amount 和 volume 欄位（lib/api.ts:115-123）。

時間軸失真也重現了。以 6196 在 9/23 為例，11:38 那根棒在迷你圖畫在寬度 98.8% 的位置，照時間對應應該只在 58.5%。各組的最大水平位移佔寬度 10.5% 到 44.1%。

有兩處細節需要修正：
1. 6196 在 9/23 實際是 83 根棒，84 根的是 2454。
2. 「其中 110 分鐘是缺口」不是缺口總數。區間 09:32-13:28 共 237 個分鐘格，缺了 154 格；110 分鐘指的是單一段最大跳空，也就是 11:38 直接跳到 13:28（缺 109 格）。

另外要補一個 finding 沒提的面向。個股圖的 VWAP 也是用局部的分鐘棒算的，不等於官方均價，這些棒只涵蓋官方成交量的 12% 到 87%。例如 2454 在 9/22，VWAP 對官方偏 +100.4bp，比迷你均線的 +77.4bp 還大。36 組裡有 16 組是迷你均線反而更接近官方；偏差中位數分別是迷你 13.5bp、VWAP 10.75bp。所以這條問題的本質是「兩個畫面口徑與時間軸不一致」，不能說成個股圖就準、迷你圖就錯。

嚴重度維持 P3。迷你圖只是自選表裡的小圖，沒有顯示均價數值，影響限於畫面不一致。
    - 證據：
      - frontend/src/components/stock-table/MiniIntraday.tsx:7 註解寫『成交均價，這裡用 close 的累計均值近似』；:62-74 cumSum += close、avg = cumSum/(i+1)；:81 xScale = (i / (n - 1)) * W
      - frontend/src/lib/intraday-chart.ts:16-26 computeIntradayAverage = 累計 amount / 累計 volume；:30-43 FULL_DAY_TIMES 共 271 格（09:00 到 13:30）
      - frontend/src/components/EChartsIntraday.tsx:421 用 computeIntradayAverage(data)；:67-80 依 formatMinuteTime 把每根棒對到 FULL_DAY_TIMES 的索引
      - frontend/src/pages/Watchlist.tsx:1859 MiniIntraday 的 rows 來自 api.klineMinuteBatch；backend/app/api/kline.py:721-743（minute-batch）與 :930-943（minute）都讀 repo 的本地分鐘 K，資料來源相同
      - frontend/src/lib/api.ts:115-123 MinuteKlineRow 含 volume、amount 欄位
      - 腳本 AUDIT_SCRATCH/work/verify-miniavg/repro.py，執行方式為 `cd backend && uv run python repro.py`，輸出存在同目錄的 repro_out.csv
      - 本地重算（finding 的數字列在括號內）：6196 9/23 mini_final=542.4458（542.45）、vwap_final=547.3584（547.36）、official_avg=969930015/1773924=546.7709；mini_vs_off=-79.1bp（-79.1bp）、vwap_vs_off=+10.7bp（+10.7bp）；mini_vs_vwap=-89.8bp（約 0.9%），當天盤中逐點最大差為 -93.7bp
      - 本地重算：共 36 組個股×交易日（36），迷你均線對官方的最大 |偏差| 是 6196 9/23 的 -79.1bp（-79.1bp）；逐點的迷你對 VWAP 最大差是 6196 9/22 的 -109.5bp
      - 線上：https://tw.stock.yahoo.com/quote/6196.TW 顯示『資料時間：2026/09/23 14:30』『均價 546』『成交 549』『總量 1,658』；(542.4458-546)/546 = -0.651%（finding：-0.65%）
      - 本地重算，6196 9/23 的棒數與缺口：rows=83（finding 寫 84，2454 在 9/23 才是 84 根）；第一根 09:32、最後一根 13:28，span 237 格，區間內缺 154 格；最大跳空是 11:38 直接到 13:28，相隔 110 分鐘；11:38 那根棒等距畫在 x=98.8%，照時間應在 58.5%，最大水平位移佔寬度 40.3%
      - 新面向：2454 9/22 的 vwap_vs_off=+100.4bp、mini_vs_off=+77.4bp；有 16/36 組是迷你均線比 VWAP 更接近官方；偏差中位數 |mini-off|=13.5bp、|vwap-off|=10.75bp；各組分鐘棒成交量只佔官方的 12.0% 到 86.6%（minute_recorder.py:134-161 的成交額只是 price×Δvolume 的近似值）
      - data/instruments_etf 是空目錄，get_etf_symbol_set 回傳空集合，4 檔 ETF 因此也讀 kline_minute，36 組在兩個畫面都有資料
      - 工作樹比對：git status 仍是原本的 3 個既有修改，本次沒有寫入 repo

### [P3 → P3] 分時圖 connectNulls: true 把沒錄到的時段連成直線，和『缺口代表沒在看』的設計相反（確認成立）

- **主張**：MinuteBarRecorder 的設計是缺段不插值，讓圖上的缺口代表沒錄到；但個股與多日分時的價格線、均價線都設 connectNulls: true，缺口被畫成直線。
- **正確的定義或數值**：缺口保持斷開（connectNulls: false），或以虛線或底色標示未錄製區段。
- **app 實際行為**：缺段被連線，看起來像有連續成交資料。
- **對使用者的影響**：使用者無法分辨這段時間是真的橫盤，還是根本沒有資料，加重均價誤判。
- **修法建議**：價格線與均價線改為 connectNulls: false；或只連接相鄰分鐘，超過 1 分鐘的缺口斷開。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/minute_recorder.py:4-7
  - frontend/src/components/EChartsIntraday.tsx:364、375
  - frontend/src/components/EChartsMultiDayIntraday.tsx:222、269
- **線上證據**：
  - 無（ECharts 設定語意；未另行查證）
- **本地計算證據**：
  - 9/23 的 2454、6196、2379 都在 11:38 到 13:27/28 之間缺 109～110 分鐘，圖上會畫成一條直線，均價線同樣如此。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：MinuteBarRecorder 的設計是缺段不插值，讓缺口代表「沒在看」。但 EChartsIntraday 與 EChartsMultiDayIntraday 的價格線與均價線都設了 connectNulls: true，ECharts 會用直線跨過 null。

9/23 實際重現：2454、6196 在 11:38→13:28 之間缺 109 格，2379 在 11:38→13:27 之間缺 108 格（時間跨度 109～110 分鐘），都被畫成一條直線；均價線兩端的值完全相同，所以是水平線。這段期間後端 log 完全靜默（11:40:02～13:27:40），當日本地錄到的成交量只占官方日量的 27%～55%，可見缺口是沒錄到，不是沒成交。9/18 也有 45～48 分鐘的同類缺口。

修法不能直接把 connectNulls 改成 false（「只連接相鄰分鐘」效果相同）。每天有 35～62 段 1～2 分鐘的小缺口，改了會把線切成 40～61 段，只有一個點的段落在 symbol:'none' 下畫不出來，連缺口之後的最後價格都會消失。應只在超過門檻的缺口（例如 5 分鐘以上）斷線，或用 markArea 標示未錄製區段，並讓孤立點顯示符號。
    - 理由：結果：finding 的核心說法成立，嚴重度 P3 合理。

1. 設計意圖：minute_recorder.py 的模組 docstring 寫明缺段不插值，「a gap in the chart means "we were not watching"」。
2. 前端實作：
   - EChartsIntraday 先把每一分鐘填成 null，再依 FULL_DAY_TIMES（09:00～13:30，共 271 格）放入收盤價與均價。價格線與均價線都設了 connectNulls: true。
   - EChartsMultiDayIntraday 每天各自是一條 series，當天沒資料的分鐘是 null，價格線與均價線同樣設 connectNulls: true。所以同一天內的缺口會被連起來，跨日則不會。
3. ECharts 的行為：
   - 官方文件說明 connectNulls 預設為 false，意思是「Whether to connect the line across null points」。
   - 本機安裝的 echarts 5.6.0 中，poly.js 的 drawSegment 遇到 null 點且 connectNulls 為真時，會跳過該點（continue），再用 lineTo 接到下一個有值的點，也就是畫出一條直線。
4. 資料會送到圖上：/api/kline/minute 判斷本地資料不完整後，因為台股分鐘源回傳空表，所以回傳 local_partial，把有缺口的原始 rows 交給前端，中間沒有補值。
5. 數值重現：用 polars 讀 data/kline_minute/date=2026-09-23，並照前端的邏輯重建陣列。
   - 2454：11:38（收 5155）到 13:28（收 5180），中間缺 109 格。
   - 6196：11:38（549）到 13:28（549），缺 109 格。
   - 2379：11:38（755）到 13:27（759），缺 108 格。
   - finding 寫「缺 109～110 分鐘」，其實是時間跨度 109～110 分鐘；實際缺的分鐘數是 108～109。差 1 格只是用詞問題，不影響結論。
   - 均價線在缺口兩端的值完全相同（2454 為 5204.78、6196 為 547.36、2379 為 769.26），因為重啟後第一根 K 棒的成交量是 0。所以均價線在缺口上是一條水平直線，「均價線同樣如此」成立。
6. 缺口確實是沒錄到，不是真的沒成交：
   - backend.log 在 11:40:02 之後沒有任何紀錄，直到 13:27:40 才恢復；恢復時第一件事是補跑一個錯過的 apscheduler 工作，接著因 watchfiles 偵測到檔案變動而重啟。
   - 官方日 K 顯示 2454 當天成交 6,430,491 股，本地分鐘資料只錄到 2,180,000 股，覆蓋率 33.9%。2379 覆蓋率 26.6%，6196 為 54.7%。
7. finding 沒提到的面向：
   - 9/18 也有同樣的長缺口：2454、6196 為 12:04→12:50，缺 45 格；2379 為 12:01→12:50，缺 48 格。
   - 除了長缺口，每天還有 35～62 段只缺 1～2 分鐘的小缺口。
8. 修法建議有瑕疵：finding 建議「connectNulls: false」或「只連接相鄰分鐘」，這兩種做法的效果相同。
   - 會把 9/23 的線切成 40～41 段、9/18 切成 55～61 段；其中 17～28 段只有一個點。
   - 這些圖設了 symbol: 'none'，只有一個點的段落不會畫出任何東西。9/23 這三檔在缺口之後只剩最後一根 K 棒（13:27 或 13:28），改成 false 後，最後價格反而會從線上消失。
   - 應改為只在超過門檻（例如 5 分鐘以上）的缺口斷線，或用 markArea 標示未錄製的區段。
9. 另一元件：自選清單的 MiniIntraday 用資料的索引當 x 座標（xScale = i/(n-1)），缺口會被整段壓掉；這是同類但不同元件的問題。
    - 證據：
      - backend/app/taiwan/realtime/minute_recorder.py:4-7 — missing stretches stay missing rather than being interpolated, so a gap in the chart means "we were not watching"
      - frontend/src/components/EChartsIntraday.tsx:68-86 closes/avgData 預設 null，依 FULL_DAY_TIMES 放值；:364（價格）、:375（均價）connectNulls: true
      - frontend/src/components/EChartsMultiDayIntraday.tsx:78-83 沒資料的分鐘推入 null；:222（價格）、:269（均價）connectNulls: true；每天各自一條 series
      - frontend/node_modules/echarts/lib/chart/line/poly.js:74-79 isPointNull 且 connectNulls 為真時 continue，接著用 lineTo 接到下一點（echarts 5.6.0；LineSeries.js:138 預設 connectNulls: false）
      - https://raw.githubusercontent.com/apache/echarts-doc/master/en/option/series/line.md — connectNulls 預設 false，文件原文："Whether to connect the line across null points."
      - backend/app/api/kline.py:946-956 本地不完整且線上源為空時，回傳 local_partial 原始 rows；backend\app\services\kline_sync.py:855-856 provider 為 taiwan 時回傳空 DataFrame
      - 指令：uv run python ...\scratchpad\audit\work\verify-connectnulls\simulate.py → 9/23 2454：11:38 close=5155 avg=5204.78 → 13:28 close=5180 avg=5204.78，缺 109 格；6196：11:38 549 → 13:28 549，缺 109 格；2379：11:38 755 → 13:27 759，缺 108 格
      - 同一腳本：9/23 本地分鐘成交量合計對官方日成交量 — 2454：2,180,000／6,430,491（33.9%）；2379：1,300,000／4,887,225（26.6%）；6196：971,000／1,773,924（54.7%）（官方來源：reference\csv\twse_stock_day_202608_202609.csv）
      - gaps.py 輸出：9/18 自選 9 檔都有午間長缺口，2454 為 12:04→12:50（45 格），2379 為 12:01→12:50（48 格）；9/17、9/22 的缺口在尾端，不會被連線
      - data/backend.log:35139-35147 最後一筆紀錄在 2026-09-23 11:40:02（行情刷新），下一筆是 13:27:40（apscheduler 補跑錯過的工作），接著 13:27:41 shutdown，13:27:49 重啟
      - isolated.py：若改為 connectNulls:false，9/23 的 2454／6196／2379 分別切成 40／41／41 段，其中只有一個點的段落為 17／20／19 段；缺口之後的最後一段只有 1 個點，在 symbol:'none' 下畫不出來
      - git：connectNulls 在 fd79b63（2026-09-11，承接 A 股時代的程式碼）就已存在，「缺口代表沒在看」的 recorder 設計則在 96892bf（2026-09-17）才加入，圖表設定之後沒有再調整

### [P3 → P3] 日K資訊列換手率公式沿用 A 股『手』的換算，多乘 100（目前台股流通股數為空，尚未觸發）（確認成立）

- **主張**：turnoverRate = volume × 100 / floatShares × 100，裡面的 ×100 是 A 股『手 → 股』的換算；台股 volume 已經是股，換手率會多 100 倍。目前台股 float_shares 固定為 None，所以不顯示。
- **正確的定義或數值**：換手率 = volume ÷ floatShares × 100%。
- **app 實際行為**：volume × 100 ÷ floatShares × 100。
- **對使用者的影響**：目前沒有影響；日後補上流通股數時，會顯示放大 100 倍的換手率。
- **修法建議**：改為 `d.volume / floatShares * 100`，兩處（getInfoBarHTML 與 initialHTML）一起改，並補單元測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/EChartsCandlestick.tsx:912、1161
  - backend/app/api/kline.py:163-166、195（float_shares 固定為 None）
  - frontend/src/lib/intraday-chart.ts:13-15（原碼註解承認 A 股是『手』、台股是股）
- **線上證據**：
  - 無需線上來源（單位換算）
- **本地計算證據**：
  - data\instruments 目錄為空，所以 _get_stock_info 會退回台股分支，float_shares 為 None，資訊列不顯示換手率。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：EChartsCandlestick.tsx 第 912 行和第 1161 行的換手率公式 volume×100/floatShares×100，是沿用 A 股的寫法：A 股 volume 以「手」為單位，所以要 ×100 換成股。台股日 K 的 volume 已經是股，本地資料和 TWSE 成交股數一致，因此算出來會放大 100 倍。以 2379 在 2026-09-23 為例，正確值 0.948%，前端會顯示 94.80%。後端 volume_units.turnover_rate_pct_expr 已經依市場換算，前端沒有。目前台股個股的 stock_info.float_shares 固定為 None（kline.py 第 195 行），ETF 則完全沒有這個欄位，所以換手率不會顯示，也不影響使用者。日後若把 issued_shares 之類的股數接進 stock_info，就會立刻顯示放大 100 倍的換手率。建議改成 d.volume / floatShares * 100，或依 symbol 後綴判斷市場換算，並補單元測試。
    - 理由：我用本地 parquet 和官方參考資料重算過，finding 說的「前端公式放大 100 倍、但目前不會顯示」兩件事都重現了，嚴重度 P3 合理。

1) 公式：EChartsCandlestick.tsx 第 912 行（getInfoBarHTML）和第 1161 行（initialHTML）都寫成 d.volume * 100 / floatShares * 100。這段在 repo 第一個 commit fd79b63 就已經存在，沿用自上游 A 股版本。後端 app/volume_units.py 第 19-30 行已經依市場換算：台股直接用 volume，A 股才 ×100（因為 A 股的 volume 單位是手）。pipeline.py 第 737 行的註解也寫明「CN volume is lots; Taiwan volume and float_shares are shares」。前端沒有這層判斷，一律乘 100。

2) 單位：本地 kline_daily_enriched 和 taiwan/daily 的 volume，我拿去跟官方 TWSE STOCK_DAY 的 volume_shares（成交股數）比對。自選 5 檔在 2026-09-22 和 09-23 共 10 列，其中 8 列完全相等（比值 1），另外 2 列是 2454，比值 0.9944 和 0.9992，量級還是股。由此可知台股 volume 的單位確實是股，不是手。

3) 放大倍數：分母用 data/taiwan/share_capital.parquet 的 issued_shares，這個數字和官方 t187ap03_L 的 issued_common_shares 完全相等。代入後，前端公式和正確公式的比值 10 列全是 100。以 2026-09-23 為例：
- 2379：正確 0.9480%，前端 94.80%
- 2454：正確 0.4004%，前端 40.04%
- 3034：正確 0.4668%，前端 46.68%
- 6196：正確 0.8051%，前端 80.51%
- 3545：正確 0.1853%，前端 18.53%
後端 enriched 在 09-23 已經寫入的 turnover_rate，和「正確」那一欄完全一致。所以後端口徑正確，只有前端放大 100 倍。

4) 目前沒觸發：data/instruments 目錄是空的（腳本輸出 []），所以 _get_stock_info 會走台股分支，回傳 float_shares: None（kline.py 第 195 行）。ETF 走 _get_asset_info，只回傳 name（kline.py 第 198-210 行）。前端條件是 floatShares && d.volume，拿到 null 就不顯示換手率。自選 9 檔目前都看不到換手率。

5) 補充：要觸發必須有人刻意改動。kline.py 第 166 行的 docstring 明文規定 float_shares 維持 null、不得用其他數字代替。不過 TaiwanShareCapitalStore 已經把 issued_shares 以 float_shares 的名稱提供給 pipeline（share_capital_store.py 第 162-174 行）。日後若把它接到 kline 的 stock_info，就會立刻顯示放大 100 倍的數字。因此維持 P3。

線上查證：WebSearch 的 session 額度已經用完。Wikipedia、百度百科、Investopedia 都抓不到（回 404、403，或工具不支援該網站）。Yahoo 和 HiStock 的 2454 頁面只有成交量，而且以張為單位（2026/09/23 分別是 5,909 張和 5,902 張），沒有周轉率，所以沒辦法跟線上周轉率直接對照。「換手率 = 成交股數 ÷ 股本 × 100%」這個定義，在本報告裡是靠 repo 後端的實作加上官方股數資料佐證，沒有線上定義原文。另外 Yahoo 和 HiStock 的成交量比 TWSE STOCK_DAY 的 6,430,491 股少，推測是口徑不同（例如沒算盤後或零股），我沒有再查。

旁支觀察（不屬於本 finding，也沒有深查）：2454 本地 volume 比 TWSE 成交股數少。09-22 少 68,000 股，09-23 少 5,000 股，其他 4 檔完全相等。
    - 證據：
      - frontend/src/components/EChartsCandlestick.tsx:912 `const turnoverRate = floatShares && d.volume ? (d.volume * 100 / floatShares * 100) : null`（getInfoBarHTML）
      - frontend/src/components/EChartsCandlestick.tsx:1161 同一公式（initialHTML）；git log -S 顯示這段在第一個 commit fd79b63 就已存在
      - backend/app/volume_units.py:19-30 後端依市場換算：台股 volume 直接當股數用，A 股才 ×100
      - backend/app/indicators/pipeline.py:737 註解 `CN volume is lots; Taiwan volume and float_shares are shares.`
      - backend/app/api/kline.py:195 `return {"name": name, "total_shares": total_shares, "float_shares": None}`；kline.py:198-210 ETF 只回傳 name
      - 本地計算：cd backend && uv run python AUDIT_SCRATCH/work/verify-g5turn/repro.py；輸出 `instruments dir files: []`
      - 本地計算：2026-09-23 本地 volume 與 TWSE STOCK_DAY 成交股數（reference csv/twse_stock_day_202608_202609.csv）：2379 4887225 對 4887225、3034 2840457 對 2840457、6196 1773924 對 1773924、3545 412175 對 412175 完全相等；2454 為 6425491 對 6430491（比值 0.9992）
      - 本地計算：分母 share_capital.parquet 的 issued_shares 與官方 twse_t187ap03_L issued_common_shares 相等（例如 2379 為 515512641、2454 為 1604952613）
      - 本地計算：2026-09-23 正確值 → 前端值：2379 0.9480%→94.80%、2454 0.4004%→40.04%、3034 0.4668%→46.68%、6196 0.8051%→80.51%、3545 0.1853%→18.53%；10 列比值全是 100；後端 enriched turnover_rate 與正確值一致（2379 為 0.9480320386556729）
      - 線上：https://tw.stock.yahoo.com/quote/2454.TW 顯示 2026/09/23 成交量「5,909 thousand shares」（以張計），沒有周轉率；https://histock.tw/stock/2454 顯示「成交量：5,902張」，沒有周轉率。定義類頁面（Wikipedia zh 回 404、百度百科回 403、Investopedia 工具無法抓取）都沒拿到內容，WebSearch 額度已用完

## 確認正確的項目

- **K線成交量副圖 VOL5／VOL10 均量線（volMaN，EChartsCandlestick.tsx:88-97；含當日、分母固定 N）**：從原始碼逐行切出函式，用 node 24 直接執行，輸入 kline_daily_enriched 9 檔自選股 2026-03-24～09-23（每檔 127 根；StockDailyKChart 預設抓 6 個月），和後端 vol_ma5／vol_ma10（pipeline.py:428-431 rolling_mean）逐日比對。另用官方 STOCK_DAY 的成交股數自己算一次。
  - 來源：frontend/src/components/EChartsCandlestick.tsx:88-97
  - 來源：backend/app/indicators/pipeline.py:428-431
  - 來源：本地：node harness.ts → 每檔 n5=123、n10=118、maxRel5=0、maxRel10=0；例：2454 9/23 前端 VOL5=9,226,733.4、後端 vol_ma5=9,226,733.4
  - 來源：本地：若改用官方成交股數，VOL5 最大相對差 2.69e-02（6196 9/21：1,216,120.2 對 1,249,720.2），原因是輸入成交量（見 findings『日K成交量輸入』），不是公式
  - 來源：邊界：載入視窗最左邊 4／9 根，前端 VOL5／VOL10 為 null，後端有值（後端有更早的歷史可暖機）；預設只看最右邊 60 根，畫面看不到這段差異
- **資訊列 VOL5／VOL10 重算（calcVolMa，EChartsCandlestick.tsx:385-394）**：照原碼在最後一根重算，和 volMaN、後端 vol_ma5／vol_ma10 比對
  - 來源：frontend/src/components/EChartsCandlestick.tsx:385-394
  - 來源：本地：fe_results.json 裡 info_vol5_last／info_vol10_last 與 volMaN、後端值完全相同，例：0050 為 79,725,343.6／77,417,270.7
- **volMaN 把 null 成交量當 0（`?? 0`）**：追蹤到 toOHLC 已先把 null 轉成 0（StockDailyKChart.tsx:70）；掃描實際資料
  - 來源：frontend/src/components/StockDailyKChart.tsx:70
  - 來源：本地：daily_enriched.json 共 1287 列，null 成交量 0 列、0 成交量 0 列，目前不會觸發。剩下的風險只有盤中即時K棒的 volume 為 None 時會被當 0（kline.py:569），屬推測性邊界
- **量比N（volumeRatioAt，EChartsCandlestick.tsx:106-119）在已收盤日：當日量 ÷ 前 N 日均量（不含當日），N 限制在 1～20**：node 直接執行原碼，N=5 和後端 vol_ratio_5d（pipeline.py:434-436、486-491：volume／shift(1).rolling_mean(5)）逐日比對；N=1、5、20 另用官方成交量獨立重算
  - 來源：frontend/src/components/EChartsCandlestick.tsx:106-119
  - 來源：frontend/src/components/StockDailyKChart.tsx:23-28（N 夾在 1～20）
  - 來源：本地：每檔 nRatio=122、maxRelRatio=0；例：2379 9/23 前端 1.6431、後端 1.6431
  - 來源：https://wiki.mbalib.com/zh-tw/量比：『量比是…開市後每分鐘的平均成交量與過去5個交易日每分鐘平均成交量之比』（收盤後，累計開市時間等於全日，所以與本式等價）
- **分時均價公式 computeIntradayAverage（intraday-chart.ts:16-26）＝ Σ成交金額／Σ成交量；每個交易日重新累計（EChartsMultiDayIntraday.tsx:66 逐 session 計算）**：對照 VWAP 定義；並檢查分鐘棒 volume 以股、amount 以元計（MIS 張×1000，mis_provider.py:164-166）
  - 來源：frontend/src/lib/intraday-chart.ts:16-26
  - 來源：backend/app/taiwan/realtime/mis_provider.py:164-166
  - 來源：https://en.wikipedia.org/wiki/Volume-weighted_average_price：『VWAP is calculated as the cumulative value traded divided by the cumulative volume traded over the chosen window』、『VWAP resets at the start of each session』
  - 來源：公式正確；結果偏差來自輸入資料的涵蓋率，見 findings
- **formatMinuteTime（intraday-chart.ts:6-11）假設時間是 UTC，再加 8 小時**：讀 4 個 kline_minute 分區的 schema；用 fastapi.encoders.jsonable_encoder 模擬 API 輸出，再用 node 執行原函式
  - 來源：frontend/src/lib/intraday-chart.ts:6-11
  - 來源：本地：4 個分區（9/17、9/18、9/22、9/23）的 schema 都是 Datetime(time_unit='us', time_zone='UTC')；jsonable_encoder 輸出 '2026-09-17T01:42:00+00:00'
  - 來源：本地：formatMinuteTime('...T01:33:00+00:00')='09:33'；3744 根分鐘棒全部落在 FULL_DAY_TIMES 內（inGrid=n）
  - 來源：潛在風險（原碼註解已寫明）：若改成台北時間字串，'...T09:33:00+08:00' 會算成 '17:33'，整張圖變空白
- **FULL_DAY_TIMES 為 09:00～13:30（共 271 格），與台股一般交易時段相同**：執行原碼；與線上來源比對
  - 來源：frontend/src/lib/intraday-chart.ts:30-43
  - 來源：本地：len=271、first='09:00'、last='13:30'
  - 來源：https://zh.wikipedia.org/zh-tw/臺灣證券交易所：『撮合成交時間為9:00至13:30』
- **MiniIntraday 昨收基準線（MiniIntraday.tsx:52-58）：優先用 prevClose，否則用 close/(1+changePct) 反算**：確認呼叫端傳入的單位
  - 來源：frontend/src/pages/Watchlist.tsx:1859（prevClose=r.prev_close、changePct=r.change_pct）
  - 來源：backend/app/taiwan/watchlist_enrichment.py:243-245（prev_close＝報價昨收；change_pct 經 _to_fraction_pct 轉成小數）
  - 來源：reference manifest：MIS 的 y 欄是『昨收／參考價』，除權息日即為參考價，所以自選頁基準線用的口徑正確；反算分支只在 prev_close 缺值時才用，小數單位正確
- **MiniCandlestick 紅綠判斷（MiniCandlestick.tsx:37-51）：實體依收盤與開盤比較；開盤等於收盤時改和前一根收盤比較**：9 檔 × 最近 12 天，共 108 根，重算並和 K 線慣例比對（開高低收與官方一致）
  - 來源：frontend/src/components/stock-table/MiniCandlestick.tsx:37-51
  - 來源：https://zh.wikipedia.org/zh-tw/K线：『陽線代表收盤價大於開盤價…陰線代表開盤價大於收盤價』；『中國大陸、韓國及台灣採用「紅升綠跌」』
  - 來源：本地：108 根中實體顏色不符 0 根；十字線 2 根（3034 與 6196 在 9/16），依前收判斷為紅。維基說十字線顏色『以異於其他兩線為原則』，這裡是刻意設計（原碼註解說明一字板要看前收），不列為錯誤
- **patchLiveCandles（liveCandles.ts）用即時開高低收覆蓋或追加當日K棒，不帶成交量**：用 node 執行原碼；追蹤所有使用者
  - 來源：frontend/src/lib/liveCandles.ts:21-27
  - 來源：frontend/src/pages/Watchlist.tsx:870-875、1244、1833（klineData 只給 MiniCandlestick 用）
  - 來源：本地：同日覆蓋時保留舊的 volume（1000）；新日追加時沒有 volume 欄位。MiniCandlestick 完全不讀 volume，畫面沒有影響
- **TaiwanIndexBar 指數漲跌點數與漲跌幅（TaiwanIndexBar.tsx:26-56，數值來自 mis_provider.py:170-174：change=round(z-y,4)，pct=round(change/y*100,4)，前端 toFixed(2)）**：把同一公式套在官方指數日資料的逐日收盤上，和官方漲跌欄與第三方網站比對
  - 來源：frontend/src/components/monitor/TaiwanIndexBar.tsx:26-56
  - 來源：backend/app/taiwan/realtime/mis_provider.py:170-174
  - 來源：本地：加權指數 9/23：48157.29-47800.17=+357.12、+0.75%，官方 MI_INDEX change_pts_signed=357.12、change_pct_signed=0.75；櫃買指數 8～9 月 37 天，漲跌點數與官方最大差 0
  - 來源：https://tw.stock.yahoo.com/quote/%5ETWII：『48,157.29』『357.12』『0.75%』『昨收 47,800.17』
- **日K資訊列漲跌幅（EChartsCandlestick.tsx:907-928、1171-1177），非除權息日**：9 檔、官方 STOCK_DAY 339 個非 X 日，用 (close-prev)/prev 和官方漲跌÷(收-漲跌) 比對（四捨五入到 2 位）
  - 來源：frontend/src/components/EChartsCandlestick.tsx:907-928
  - 來源：本地：比對 339 天，四捨五入後最大差 0（除權息日另見 findings）
- **主圖與副圖指標只做顯示轉送：MA5/10/20/60、BOLL 上下軌與中軌（=ma20）、MACD DIF/DEA/柱、RSI6/14/24、KDJ K/D/J**：讀程式碼：前端只做 Number()、toFixed(2/3/1)，MACD 柱依正負上色，不重算；數值正確性屬其他領域（A 系列）
  - 來源：frontend/src/components/EChartsCandlestick.tsx:187-310
  - 來源：frontend/src/components/EChartsCandlestick.tsx:682-708、936-945
- **分時圖右軸百分比、分時多日圖 changePct、分時線顏色的公式本身 (v-prevClose)/prevClose**：讀程式碼；非除權息日的口徑與官方一致（同上面 339 天驗證）
  - 來源：frontend/src/components/EChartsIntraday.tsx:333-350、423-428
  - 來源：frontend/src/components/EChartsMultiDayIntraday.tsx:398-400
  - 來源：除權息日的基準另見 findings

## 無法驗證的項目

- **Yahoo 奇摩股市的分時『均價』曲線（逐分鐘）**：https://tw.stock.yahoo.com/quote/2454.TW 的分時圖區塊顯示『資料載入中』，是前端動態產生，抓不到。只拿到收盤後的日均價（2454 為 5,192、6196 為 546.00、2379 為 772），已用在 findings 的比對。
- **盤中『量比N』在即時K棒上的實際數值**：稽核時是 2026-09-24 凌晨，已收盤，沒有即時K棒；也不能啟動後端或輪詢。偏差是由程式碼推導出來的（前端值 = 後端折算值 × session_progress），沒有實際量測。
- **TaiwanIndexBar 盤中使用的 MIS 指數 y（昨收）與 z**：reference 的 MIS 快照不含 tse_t00／otc_o00，而且禁止連 MIS。只驗證了公式與官方收盤。另外注意 Yahoo 備援會把 t00.TWSE 轉成 't00.TW'（yahoo_provider.py:27-36），不是 ^TWII，所以 MIS 失敗時指數沒有 Yahoo 備援（推測會顯示 '--'，不會給錯數）。
- **收盤集合競價（13:30）那筆成交是否被分鐘錄製丟掉**：9/23 錄製在 11:38 中斷、13:27/28 才恢復；9/18 最後一根是 13:27/13:28。程式碼上 record() 以牆鐘判斷 get_market_status()==OPEN（minute_recorder.py:81；calendar.py:118 在 13:30:00 之後就是 POST_CLOSE），推測 13:30 那筆會被丟掉。但資料中沒有程式確定持續跑到收盤之後的日子可以證實，標為推測。
- **KD、MACD、RSI、MA、BOLL 的數值本身與第三方網站比對**：這些不在本領域範圍（前端只轉送）；由其他領域（A 系列）驗證。
