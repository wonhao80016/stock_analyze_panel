# A03-kd-rsi｜KD/KDJ 與 RSI

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 4 | 3 | 0 | 0 | 0 | 15 | 5 |

## 稽核者摘要

結論：盤後全量管線（pipeline.compute_indicators → kline_daily_enriched）算出的 KD(9,3,3)、J＝3K−2D 與 Wilder RSI 6/14/24 公式正確。這個結論有三方面佐證：一是自己獨立實作後比對，9 檔自選股在 2026-08-13～09-23（30 個交易日）的最大差異是 1.7e-11；二是第三方比對，9 檔股票 9/22、9/23 兩天共 18 個值與 TradingView 的 RSI(14) 位元級一致；三是輸入的 OHLC 與證交所 STOCK_DAY 342 列完全相同。增量路徑的 KD 也與全量等價。

主要問題有七項，依嚴重度排列：
(1) P1：台股指標用未還原分割的價格計算。0050 在 2025-06-18 一拆四，之後的 RSI14 有 36 天被假算成低於 30，RSI24 最大偏差 50 點；到今天的「1年」K 線起點，RSI24 仍偏差約 30 點。TradingView 的 0050 RSI 與「還原分割」後的計算完全相等，可以反證這一點。
(2) P2：台股選股器、個股比較頁、AI 研究也都標示為「RSI(14)」，實際卻是 Cutler 簡單平均。9/23 與其他頁面相差最多 13.3 點（00893 為 83.3 對 70.1），00878 還因此被跨過 70 的超買門檻。
(3) P2：pipeline 的 RSI 與 KD 沒有暖機門檻。329 檔新上市標的在上市首日 RSI 一律為 0；KD 也沒有台灣慣例的初值 50。
(4) P3：盤中增量 RSI 的遞推狀態只用 90 個日曆天計算，RSI24 與收盤後重算的值最多差 2.95 點。
(5) P3：K 線自訂起日若落在 300 天快取的前段，沒有任何暖機，RSI 誤差最多約 61 點。
(6) P3：自選頁的台股列沒有 KDJ、RSI6、RSI24；RSI14 在盤中是前一日的值。
(7) P3，潛在問題：RSV 分母為 0 時產生 NaN，KD 之後會永久變成 null，目前資料中 0 例。

KD 的第三方數值抓不到，各網站都是動態頁面或回應 403。本次只讀，工作樹比對前後一致。腳本與輸出位於 AUDIT_SCRATCH/work/A03-kd-rsi/（step1～step5 的 .py 與 .out 檔）。

## 問題清單

### [P2 → P2] 台股選股器、個股比較頁與 AI 研究的「RSI(14)」實為 Cutler 簡單平均，與全站 Wilder RSI14 最多差 13 點（確認成立）

- **主張**：三處地方用過去 14 日漲跌的簡單平均計算 RSI，標籤卻同樣寫「RSI(14)」，與自選頁、K 線及策略使用的 Wilder RSI14（TradingView 同口徑）數值不同。平盤時的邊界處理也互相矛盾：一處給 0，另一處給 100。
- **正確的定義或數值**：同一個標籤「RSI(14)」在全站應採用同一口徑，也就是與台灣財經網站及 TradingView 一致的 Wilder 平滑；平盤時的邊界值應一致，或回傳 null。
- **app 實際行為**：選股器的篩選、排序、標色，比較頁，以及餵給 AI 的 rsi14，都使用 Cutler 口徑，數值與自選頁、K 線不同，差距足以跨越 70/30 門檻。
- **對使用者的影響**：使用者在選股器用「RSI(14) 區間」篩選，得到的結果與自選頁、K 線顯示的 RSI14 不符；00878 在選股器被標紅為超買（77.5），自選頁卻顯示 69.3。比較頁與 AI 研究的結論也建立在不同口徑上。
- **修法建議**：選股器與 research_context 改用共用的 Wilder RSI（例如 technical_indicators 或 pipeline 的同一式），並把回看期拉長到足以收斂（至少 100 根以上）；平盤邊界統一處理，前端改用 `!= null` 判斷。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:296 只取 35 個分區；screener.py:318-320 rolling_mean(14)，並以 loss==0→0.00001 處理
  - backend/app/taiwan/research_context.py:457-467 取最近 14 個 diff 的簡單平均；avg_loss==0 時回傳 100.0
  - frontend/src/pages/TaiwanStockCompare.tsx:360-363 顯示「RSI(14)」＝technical_context.rsi14
  - backend/app/taiwan/ai_research.py:200 "rsi14": ctx.technical_context.rsi14
  - frontend/src/pages/TaiwanScreener.tsx:1721-1722 `item.rsi_14 ?`，值為 0 時顯示「-」；≥70 標紅
  - backend/app/taiwan/technical_indicators.py:18-22 註解已自承這是 pre-existing divergence
- **線上證據**：
  - MoneyDJ RSI 條目採威爾德平滑法：「UP t = UP t-1 + 1 / N ( Ut – UP t-1)」 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9
  - StockCharts 的 Wilder 定義，https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
  - TradingView 的 RSI(14) 與 app 的 Wilder 值位元級一致（例如 00893 為 70.05910765392113），與 Cutler 值不一致
- **本地計算證據**：
  - 以 screener.py 的公式重算 2026-09-23，Cutler 對 Wilder：00893 為 83.33 對 70.06（差 13.27）；00878 為 77.45 對 69.26（跨越 70 門檻）；0050 為 73.06 對 66.92；3034 為 57.76 對 51.54；6196 為 66.67 對 61.47（step2.out 的 (d) 段）
  - 在 8/13～9/23 的已存窗口中，每檔的 |Wilder−Cutler| 最大值介於 9.9 與 25.1 之間（step1.out 的 max|R14-Cutler| 欄）
  - 全庫共 30 列最近 14 日收盤價完全不變；這種情況選股器的公式會得到 0，research_context 會得到 100，pipeline 的 EWM 則是 26～48
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：台股選股器（screener.py:317-321）與 research_context（research_context.py:456-467）的「RSI(14)」都是用過去 14 日漲跌簡單平均算的 Cutler RSI。後者的值會進入個股比較頁（TaiwanStockCompare.tsx:360-363）與 AI 研究 payload（ai_research.py:200）。自選頁、K 線與 pipeline 用的則是 Wilder 平滑（alpha=1/14），這也是 XQ 與 TradingView 的口徑。

9/23 自選檔的差距：00893 為 83.33 對 70.06，00878 為 77.45 對 69.26，0050 為 73.06 對 66.92。全市場在 70 與 30 門檻上判定不一致的分別有 98 檔與 95 檔（共 1330 檔），選股器判為 ≥70 的有 141 檔，Wilder 只有 49 檔。

平盤時兩處的邊界值矛盾：選股器給 0，research_context 給 100。實例只有 5523.TPEX 一檔。另外，選股器會把 null close 當成 0 漲跌，停牌標的因此得到 0；前端顯示「-」，後端的 ≤30 篩選卻仍會納入。

finding 中「TradingView 位元級一致」與「全庫 30 列平盤」兩項證據不成立或未重現。嚴重度維持 P2，但接近 P1（錯誤金融口徑）的邊界。
    - 理由：核心主張成立。程式碼、權威定義、本地重算三方面都對得上。

一、程式碼（現況行號與 finding 相同，沒有偏移）
- 選股器 screener.py:317-321：對過去 14 個漲跌取 rolling_mean(14)，也就是 Cutler 的簡單平均；loss==0 時改用 0.00001。
- research_context.py:456-467：取最後 14 個 diff 的簡單平均；avg_loss==0 時直接回傳 100.0。這裡明明讀了 65 個分區（:385-386），卻只用 14 個 diff。
- 以下三處都走 Wilder（alpha=1/14、adjust=False 的 EWM）：
  - 自選頁：watchlist_enrichment.py:150-159，回看 150 個分區，交給 technical_indicators.py:89-103 計算。
  - K 線：kline.py:432-435，讀本地日線後交給 compute_enriched。
  - pipeline.py:529-548。
- technical_indicators.py:18-22 的註解已承認選股器的公式不同。
- 前端與 AI 的使用點：
  - TaiwanScreener.tsx:1721-1723 用 `item.rsi_14 ?` 判斷有無值，所以 0 會顯示「-」；≥70 標紅、≤30 標綠。
  - TaiwanStockCompare.tsx:360-363 以「RSI(14)」標籤顯示 technical_context.rsi14。
  - ai_research.py:200 把同一個 rsi14 放進給 LLM 的 payload。
  - screener_nl.py:110 讓自然語言選股也對應到這個 rsi_14 篩選。
- 平盤時的邊界值確實矛盾：
  - 選股器：gain=0、loss=0 時 RS=0/1e-5=0，RSI=0。
  - research_context：回傳 100。
- 這個問題不在 code-review-2026-09-22.md 的已知清單裡，用 grep 搜 RSI/Cutler/Wilder 只找到無關的 A5/A6 段落。

二、正確定義（另找了兩個與 finding 不同的來源）
- XQ 全球贏家官方論壇：客服說明系統 RSI 先以 Average 取初值，之後用 sumUp = sumUp[1] + (up - sumUp[1]) / length 遞推，這就是 Wilder 平滑。XQ 是台灣最常用的看盤軟體之一。
- TradingView 繁中說明頁：列出 avgGain = rma(gain, 14)，rma 即 Wilder 平滑。
- Wikipedia：Wilder 原始定義是 (prevval*(n-1)+newdata)/n；Cutler's RSI 是改用 SMA 的變體，而且被明確稱為另一種指標（為了避免 Data Length Dependency）。

結論：標成「RSI(14)」的業界標準口徑是 Wilder，全站其他地方也用 Wilder。Cutler 雖然是有文獻的變體，但套用同一個標籤就是口徑不一致。

三、本地重算（只讀 parquet，交給純計算函數處理）
- 9/23 自選各檔，選股器（Cutler）對自選頁式 Wilder（150 分區）：

  | 代號 | 選股器 | 自選頁 |
  |---|---|---|
  | 00893 | 83.33 | 70.06 |
  | 00878 | 77.45 | 69.26 |
  | 0050 | 73.06 | 66.92 |
  | 3034 | 57.76 | 51.54 |
  | 6196 | 66.67 | 61.47 |
  | 2454 | 74.07 | 72.15 |

  與 finding 的數字逐一吻合。research_context 的重算值也相同，例如 00893 為 83.33。
- 全市場 9/23 共 1330 檔可比：
  - |差| 平均 5.75、中位數 4.86、p90 11.59。
  - 在 ≥70 門檻上判定不一致的有 98 檔，≤30 門檻上有 95 檔。
  - 選股器判為 ≥70 的有 141 檔，Wilder 只有 49 檔。
- 所以用「RSI(14) 區間」篩選或排序時，結果與自選頁、K 線的顯示確實有實質落差。在使用者情境下一定會發生：選股器是純台股的核心頁，自選的 00878 會在選股器被標紅，自選頁卻顯示 69.3。

四、finding 中不成立或誇大的周邊證據（不影響核心結論）
1. 「TradingView 與 app 的 Wilder 值位元級一致（00893＝70.05910765392113）」無法成立。TradingView 頁面是動態產生的，抓不到數值。這個數字其實等於我用全庫 661 個分區、以 app 自己的公式算出的 70.059107653921，應該是本地計算，不是 TradingView 的讀數。而且自選頁實際回看 150 個分區，顯示的是 70.0594。
2. 「全庫共 30 列最近 14 日收盤價完全不變」「pipeline EWM 是 26～48」都沒有重現。
   - 選股器各檔最新一列中，g=0 且 l=0 的只有 5 檔，其中 4 檔（00625K、2949、3064、6762）最新 close 是 null。
   - 真正 15 個非空收盤價全部相同的只有 5523.TPEX 一檔：選股器給 0.0、Wilder 為 48.19，research_context 會給 100。
   - 平盤邊界值矛盾本身是成立的，只是筆數與數值範圍不對。

五、新面向（補充）
選股器的 diff 遇到 null close 時會被當成 0 漲跌，停牌或無成交的標的因此得到 RSI=0。前端把它顯示成「-」，但後端篩選 rsi_14_max≤30 時仍會納入它（screener.py:502-503）。

六、嚴重度
維持 P2：沒有資料破壞，也不涉及下單；Cutler 是有文獻的變體，屬於同標籤、不同口徑的明確功能錯誤。但依 CONTRIBUTING.md:295，P1 包含「錯誤金融口徑」，這一項接近 P1 的邊界；如果重視篩選門檻不一致的 98/95 檔影響，可以升為 P1。
    - 證據：
      - backend/app/taiwan/screener.py:296 取最近 35 個分區；:317-321 gain/loss 用 rolling_mean(14)（Cutler），loss==0 時代入 0.00001，因此平盤得 RSI=0
      - backend/app/taiwan/screener.py:500-503 rsi_14_min/max 篩選直接用這個 Cutler 值；screener_nl.py:110 自然語言選股也對應到它
      - backend/app/taiwan/research_context.py:385-386 讀 65 個分區，但 :456-467 只用最後 14 個 diff 做簡單平均；:463-464 avg_loss==0 時回傳 100.0
      - backend/app/taiwan/ai_research.py:200 "rsi14": ctx.technical_context.rsi14 送進 LLM；frontend\src\pages\TaiwanStockCompare.tsx:360-363 以「RSI(14)」標籤顯示同一值
      - frontend/src/pages/TaiwanScreener.tsx:1721-1723 用 `item.rsi_14 ?` 判斷（0 會顯示「-」），≥70 標紅、≤30 標綠
      - Wilder 路徑：technical_indicators.py:89-103（ewm alpha=1/14, adjust=False）；watchlist_enrichment.py:150-159（回看 150 個分區）；pipeline.py:529-548；kline.py:432-435（compute_enriched）；technical_indicators.py:18-22 註解承認選股器公式不同
      - XQ 官方論壇（https://forum.xq.com.tw/thread/%E8%AB%8B%E5%95%8F%E7%B3%BB%E7%B5%B1%E7%9A%84%EF%BD%92%EF%BD%93%EF%BD%89%E7%9A%84%E8%A8%88%E7%AE%97%E6%96%B9%E5%BC%8F-1/）：初值 sumUp = Average(maxlist(price - price[1], 0), length)，之後 sumUp = sumUp[1] + (up - sumUp[1]) / length；RSI = 100 * sumUp / (sumUp + sumDown)（即 Wilder）
      - TradingView 繁中說明（https://tw.tradingview.com/support/solutions/43000502338/）："avgGain = rma(gain, 14)  avgLoss = rma(loss, 14)"
      - Wikipedia（https://en.wikipedia.org/wiki/Relative_strength_index）："Wilder originally formulated the calculation of the moving average as: newval = (prevval * (n - 1) + newdata) / n"；Cutler's RSI 改用 SMA 以避免 "Data Length Dependency"
      - 本地重算：uv run python scratchpad\audit\work\verify_rsi_def\v1.py（輸出 v1.out）。9/23 Cutler 對 Wilder150：00893 83.3333 對 70.0594；00878 77.4545 對 69.2555；0050 73.0627 對 66.9203；3034 57.7586 對 51.5404；6196 66.6667 對 61.4694；2454 74.0741 對 72.1548；research_context 重算值與選股器相同（83.33/77.45/73.06…）
      - v1.out 全市場 9/23 共 1330 檔：|差| mean 5.75、median 4.86、p90 11.59；≥70 判定不一致 98 檔、≤30 不一致 95 檔；選股器 ≥70 有 141 檔，Wilder 有 49 檔
      - v2.out/v3.out：選股器最新列 g=0 且 l=0 的有 5 檔（其中 4 檔 close 為 null），真正 15 個非空收盤價全同的只有 5523.TPEX（選股器 0.0、Wilder 48.19）；finding 所稱「30 列」、「EWM 26～48」未重現
      - v1.out：全庫 Wilder 對 00893 算出 70.059107653921，與 finding 所稱 TradingView 值相同；自選頁的 150 分區值為 70.0594，可見該數字是本地計算，不是 TradingView 讀數（TradingView Pine 參考頁為動態產生，WebFetch 抓不到內容）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：成立。台股選股器（screener.py:318-321，只取 35 個分區）、比較頁與 AI 研究的 rsi14（research_context.py:457-467）用的是 14 日簡單平均的 Cutler RSI；自選頁（watchlist_enrichment.py，150 個分區）與 K 線（compute_enriched）用的是 Wilder RSI14，兩者標籤卻同樣是「RSI(14)」。以 2026-09-23 的官方收盤價重算（與本地 parquet 逐筆一致），自選 9 檔的差距最大在 00893：83.33 對 70.06，差 13.27。00878 在選股器是 77.45（標紅超買），自選頁是 69.26。範圍補正：「最多差 13 點」只適用 9 檔自選；全市場最新一日有 2312 檔可比，|差| 中位數 4.84、p90 12.6、最大 48.2，341 檔的 70/30 分類相反。平盤邊界上，選股器給 0（前端顯示「-」），research_context 給 100，Wilder 給 26～48。全庫歷史上共有 30 列這種情況（1586.TPEX、5523.TPEX），目前只有 5523.TPEX 的最新列受影響。
    - 理由：我自己寫腳本重算，finding 裡所有可量化的數字都完全重現。
一、程式碼：
- 選股器用 14 日簡單平均（Cutler），只取最近 35 個分區（screener.py:296、318-321）。
- research_context 用最近 14 個 diff 的簡單平均，avg_loss==0 時回傳 100（research_context.py:457-467）；比較頁走 comparison.py:211 的 research service，顯示在 TaiwanStockCompare.tsx:360-363；AI 研究經 ai_research.py:200 取用。
- 自選頁走 watchlist_enrichment.py:152-159，用 150 個分區呼叫 compute_taiwan_daily_indicators（Wilder EWM，alpha=1/14，technical_indicators.py:88-100）。
- K 線走 kline.py:433-435 的 compute_enriched，也就是 pipeline.py:529-548 的 Wilder EWM。
- 前端 TaiwanScreener.tsx:1721 用 `item.rsi_14 ?` 判斷，所以值為 0 時會顯示「-」。
- technical_indicators.py:18-22 自承這是既有的分歧。

二、數據：
- 官方 TWSE STOCK_DAY 收盤價與本地 parquet 的比對：9 檔自選各 38 筆（8/1～9/23），不一致 0 筆。用官方收盤價算出的 Cutler 值與選股器的值逐檔相同。
- 9/23 的 Cutler 對 Wilder 全部與 finding 一致。
- 8/13～9/23 各檔的 max|Wilder−Cutler| 介於 9.94 與 25.06，與 finding 的 9.9～25.1 一致。
- 全庫 14 日收盤價不變的列確實是 30 列，這些列上選股器為 0、research_context 為 100、Wilder 為 26.46～48.20。
- 00893 的 70.05910765392113：我用全歷史 EWM 算，以及用 TradingView ta.rsi 定義（RMA 以 SMA 起始）在 661 根上算，都得到逐位元相同的值。

三、需要補正的範圍描述（不影響成立）：
- 「最多差 13 點」只適用 9 檔自選在 9/23。全市場最新一日有 2312 檔可比，|差| 的中位數 4.84、p90 12.60、最大 48.19。1131 檔差距 ≥5 點，406 檔 ≥10 點，341 檔在 70/30 門檻的分類結果相反。所以 finding 低估了影響，而不是誇大。
- 「30 列」是 2024-01-02～2026-09-23 全歷史、2 檔的累計（1586.TPEX 13 列、5523.TPEX 17 列）。目前只有 5523.TPEX 的最新列落在這種邊界。
- 自選頁實際用 150 個分區，00893 為 70.05936，與全歷史值在小數第 4 位才不同，不影響結論。
- 「與 TradingView 位元級一致」我無法連線驗證，只能證明演算法定義相同、數值收斂。

嚴重度：這是同一標籤下口徑不一致，會改變篩選與超買超賣標色的結果。Cutler RSI 本身是既有的變體，不算資料破壞，維持 P2 合理。
    - 證據：
      - 程式碼 backend/app/taiwan/screener.py:296：available_dates[len-35]；screener.py:318-321：rolling_mean(14)，loss==0 時以 0.00001 取代，所以 gain=loss=0 會得到 0
      - 程式碼 backend/app/taiwan/research_context.py:457-467：最近 14 個 diff 的簡單平均，avg_loss==0 時 rsi14=100.0；comparison.py:211 由比較頁使用；ai_research.py:200 送給 AI
      - 程式碼 backend/app/taiwan/watchlist_enrichment.py:152-159：自選頁取 available[-150:]，呼叫 compute_taiwan_daily_indicators（technical_indicators.py:88-100 的 Wilder ewm，alpha=1/14）；kline.py:433-435 的 compute_enriched 對應 pipeline.py:529-548，同為 Wilder
      - 程式碼 frontend/src/pages/TaiwanScreener.tsx:1721-1722：`item.rsi_14 ?`，值 ≥70 時加 text-red-400；TaiwanStockCompare.tsx:360-363 以「RSI(14)」顯示 technical_context.rsi14
      - 官方比對（repro2.out [1]）：9 檔 official_rows=38、local_rows=38、close_mismatch=0；以官方收盤價算的 Cutler 值：2454=74.07、3034=57.76、2379=65.34、6196=66.67、3545=55.23、0050=73.06、00878=77.45、00885=42.44、00893=83.33，與選股器公式在本地 parquet 算出的值相同
      - 並列比較 9/23（repro.out）。選股器（Cutler）對自選頁（Wilder, 150 個分區），括號內為 finding 的數字：00893 為 83.33 對 70.06（finding 83.33 對 70.06，差 13.27）；00878 為 77.45 對 69.26（finding 77.45 對 69.26）；0050 為 73.06 對 66.92（finding 相同）；3034 為 57.76 對 51.54（finding 相同）；6196 為 66.67 對 61.47（finding 相同）。另外 2454 為 74.07 對 72.15，2379 為 65.34 對 60.10，3545 為 55.23 對 54.14，00885 為 42.44 對 47.43
      - 全精度（repro2.out [2]、repro7.out）：00893 的全歷史 EWM Wilder 與 SMA 起始 Wilder（TradingView ta.rsi 定義，661 根）都是 70.05910765392113，與 finding 的數字逐位元相同；自選頁 150 個分區版本為 70.0593593429801，K 線為 70.05912007641452
      - 窗口最大差（repro2.out [3]）：8/13～9/23 各檔 max|Wilder−Cutler| 範圍為 9.94～25.06（3034=9.94、6196=25.06），與 finding 的 9.9～25.1 一致；同窗口 70/30 分類相反的列共 34 列
      - 平盤邊界（repro5.out、repro6.out）：全庫 1,435,948 列中，連續 14 個 diff 皆為 0 的非空收盤價列共 30 列，屬於 2 檔（1586.TPEX 2025-07-17～08-04 共 13 列、5523.TPEX 2026-08-25～09-22 共 17 列）；Wilder EWM 範圍 26.46～48.20。最新一日只有 5523.TPEX 受影響，選股器為 0.0（前端顯示「-」），research_context 為 100，自選頁 Wilder 為 48.19
      - 全市場最新一日（repro7.out）：2312 檔可比，|選股器−自選頁| 中位數 4.84、p90 12.60、最大 48.19（5523.TPEX）；1131 檔 ≥5 點、406 檔 ≥10 點、341 檔 70/30 分類相反，所以 finding 標題「最多差 13 點」只對 9 檔自選成立
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-rsi14repro/repro{,2,3,4,5,6,7}.py；輸出存在同目錄的 *.out；執行前後 git status 與 data\taiwan 均無變化
      - 未驗證：TradingView 頁面上的實際數值（未連線）；只驗證其 ta.rsi 定義在本地資料上與 app 的 Wilder 值相同

### [P3 → P3] 盤中增量 RSI 的遞推狀態只用 90 日曆天計算，RSI24 與收盤後全量值最多差 2.95 點（確認成立）

- **主張**：live_agg 的 _rsi_avg_gain/_rsi_avg_loss 從 df_hist（latest−90 日曆天）的第一列以 0 為種子重新做 EWM，而全量計算使用完整歷史或 300 日窗口。alpha 越小，殘差越大。
- **正確的定義或數值**：盤中增量結果應與收盤後全量重算相等（KD 已經做到）。
- **app 實際行為**：RSI24 與 RSI14 在盤中有偏差，收盤後重算時會跳動。
- **對使用者的影響**：影響盤中 K 線活蠟燭的 RSI 與以 rsi_24、rsi_14 為門檻的監控規則；使用者目前監控規則為 0 條，只有圖表上輕微跳動。
- **修法建議**：RSI 的遞推狀態改從 _enriched_history_cache 的 300 日全量結果取最後一列（與 kdj_k、kdj_d 相同做法），或讓 compute_indicators 保留 _rsi_avg_* 欄位供 live_agg 直接使用。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)
  - backend/app/repository.py:816-827 在 df_hist 上重做 gain/loss 的 ewm_mean(alpha=1/n) 作為遞推狀態
  - backend/app/indicators/pipeline.py:1556-1569 增量 RSI 從上述狀態遞推一步
  - 對照：同一檔 repository.py:769-777 的 KD 狀態取自完整歷史快取，因此 KD 等價
- **線上證據**：
  - StockCharts：「A formula will need at least 250 data points to replicate our RSI numbers」，說明 Wilder 平滑需要長暖機
- **本地計算證據**：
  - 以 9 月 17 個交易日各當成今天，重建 live_agg 並跑 compute_enriched_today，與全量比較：rsi_24 最大差 2.949（2379）、rsi_14 最大差 0.626、rsi_6 最大差 0.0012、KD 差 1e-14（step2.out 的 (b) 段）
  - 9/23 的 2379：增量 RSI24 為 54.37，全量 55.49
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：live_agg 的 RSI 遞推狀態（repository.py:816-833）只用 B−90 日曆天（約 63 個交易日）的收盤價，並以 0 為種子重做 EWM。收盤後的全量值則用全歷史（enriched_refresh）或 300 日曆天快取（refresh_cache），因此盤中增量 RSI 與收盤後重算不相等；KD 取自 300 日快取的最後一列，兩者等價（差 1.4e-14）。以 2026-09 的 17 個交易日重現，自選 9 檔的數字與原 finding 完全一致：rsi_24 最大差 2.95（2379，9/18：48.18 vs 51.13）、rsi_14 最大差 0.63、rsi_6 最大差 0.0012；9/23 的 2379 RSI24 是 54.37 vs 55.49。「最多差 2.95 點」只適用於自選 9 檔。全市場 rsi_24 的 |差| 中位數 0.71、p99 3.98，最大 15.0（00685L，受未還原的分割放大）；rsi_14 在交易稀疏的標的最大 15.8，一般標的最大 5.9。以目前設定，盤中增量只算輪詢到的自選與規則標的，所以使用者實際受影響的是自選檔：活蠟燭的 RSI24／RSI14 會偏低約 1 至 3 點（153 列中 130 列偏低），收盤後重算時會跳動。
    - 理由：我自己寫了腳本，直接呼叫 repo 裡的 KlineRepository._build_live_agg（以 SimpleNamespace 當 self，只寫記憶體屬性，不寫檔）和 compute_enriched_today。在 2026-09 的 17 個交易日裡，每天 T 都當成「今天」：_enriched_history_cache 用 compute_indicators 算 [T−300 日曆天, T]，基準日 B 取前一個交易日，今天的輸入用 raw OHLC。結果和兩種全量值比較：300 日窗口的重算（f300），以及磁碟分區上收盤後用全歷史寫入的值（st）。自選 9 檔的數字與 finding 逐項吻合：rsi_24 最大差 2.9495（2379，9/18），rsi_14 最大差 0.6262，rsi_6 最大差 0.00116，KD 差 1.4e-14；9/23 的 2379 增量 RSI24 為 54.3683，全量為 55.4940（st 為 55.4936）。另外用不依賴 app 程式碼的純 Python 遞推再算一次，也得到 w90 54.3683、full 55.4936。成因也確認了：live_agg 只取 B−90 日曆天（約 63 列），gain/loss 從 0 起算做 EWM；0 種子剩下的權重是 (1−1/n)^62，n=24 時 0.071、n=14 時 0.010、n=6 時 1.2e-5，和誤差大小的排序一致。KD 狀態取自 300 日快取，所以與 f300 等價。本地收盤價與官方 TWSE STOCK_DAY 逐筆比對 380 列，差值都是 0，輸入資料正確。

需要補充的範圍：finding 標題說「最多差 2.95 點」，這只是自選 9 檔的最大值。若把全市場 2,343 檔一起算，rsi_24 的 |差| 中位數是 0.71，p95 2.59、p99 3.98，最大 15.02（00685L）。37,274 列中有 12,872 列超過 1 點，1,115 列超過 3 點。rsi_14 最大 15.79（6236.TPEX，90 天內只有 10 列）；只看 90 天窗口至少 60 列的標的，rsi_14 最大仍有 5.93。最大值裡有一部分被未還原的分割放大，例如 00685L 在 2026-07-07 單日 −96%，這屬於另一個問題。不過盤中增量路徑只處理輪詢到的自選與規則標的（kline.py:545-547 的註解），而使用者規則為 0 條，所以以使用者實際情境而言，影響就是 finding 所列的自選範圍。這個誤差只在盤中存在，收盤後 enriched_refresh 會以全歷史覆寫，因此維持 P3。還有一點小修正：KD 狀態來自 300 日曆天快取（repository.py:570,626），不是「完整歷史快取」；不過它與全歷史值的差在自選檔只有 1.4e-14，不影響結論。
    - 證據：
      - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)；repository.py:761-763 df_hist 只取 [latest−90, latest]；repository.py:816-833 在 df_hist 上以 gain/loss.ewm_mean(alpha=1/n, adjust=False) 重算 RSI 遞推狀態（首列 diff 為 null，when(>0) 條件判為 false，因此 gain/loss 首值為 0.0）
      - backend/app/repository.py:766-779 KD／EMA／ATR 狀態取自 _enriched_history_cache 最後一列；該快取在 repository.py:570,589,626 以 300 日曆天窗口 compute_indicators 建立
      - backend/app/indicators/pipeline.py:1556-1569 增量 RSI 從 _rsi_avg_gain/loss 遞推一步；pipeline.py:554-560 compute_indicators 會 drop 掉 _rsi_avg_* 欄位，所以 live_agg 必須另外重算
      - backend/app/taiwan/enriched_refresh.py:68-72,81,95 收盤後以全部歷史 compute_enriched 覆寫分區
      - backend/app/api/kline.py:576-584 盤中活蠟燭的 rsi_6/14/24 直接取自增量 enriched；kline.py:545-547 註解說明輪詢只涵蓋自選與規則標的
      - 本地重現（指令：cd backend && uv run python ...\work\verify-rsi-live\s1_repro.py，輸出 s1.out）自選 9 檔、9 月 17 個交易日共 153 列：|inc−f300| 最大值為 rsi_6 0.00116、rsi_14 0.626205、rsi_24 2.949525（2379.TWSE 2026-09-18：inc 48.1811 vs f300 51.1306 vs st 51.1302），kdj_k／kdj_d 1.4211e-14。finding 對應數值為 0.0012／0.626／2.949／1e-14，一致
      - 2379.TWSE 2026-09-23：inc_rsi_24 54.368304、f300 55.493953、st 55.493639。純 Python 手算（s4_handcalc.py，s4.out）：w90 54.3683、w300 55.4940、full 55.4936；rsi_14 為 w90 60.0036 vs full 60.0983。finding 對應數值為 54.37 vs 55.49，一致
      - 全市場（s2_allsym.py，s2.out），2,343 檔、37,274 列：|d rsi_24| 中位數 0.7056、p95 2.5895、p99 3.9759、最大 15.0209（00685L.TWSE 9/18），>1 點 12,872 列，>3 點 1,115 列；|d rsi_14| 最大 15.7933（6236.TPEX，win90 只有 10 列）；win90 ≥ 60 列時 rsi_14 最大 5.9349、rsi_6 最大 0.0225
      - 0 種子剩餘權重 (1−1/n)^N（s3.out）：N=62 時 n=24 為 0.0715、n=14 為 0.0101、n=6 為 1.2e-5；N=200（300 日窗口）時 n=24 為 0.0002，對應 f300 與全歷史 st 的差值：自選檔 rsi_24 最大 0.0053
      - 官方參考資料比對（s3_official.py）：twse_stock_day_202608_202609.csv 共 380 列（9 檔自選加 2330），本地 raw_close 全部對上，最大 |差| = 0.0
      - 00685L.TWSE 在 2026-07-07 單日 close 變動 −96%（12.23），屬於未還原的分割，放大了全市場最大值；本地 1,423,597 列中 close != raw_close 的列數為 0（Taiwan 沒有套用復權因子）
      - 腳本與輸出位於 AUDIT_SCRATCH/work/verify-rsi-live/（s1_repro.py、s2_allsym.py、s3_official.py、s4_handcalc.py 及對應的 .out 與 s1_result.parquet）；執行後 git status 與開始時相同

### [P3 → P3] K 線自訂起日落在 300 日快取前段時沒有暖機，首批 K 棒的 RSI 與 KD 大幅失真（確認成立）

- **主張**：get_daily 只要 hist_min <= start 就直接切取記憶體快取，沒有確保 start 之前有暖機；而快取本身是從 hist_min 起「從零開始」計算的。相較之下，它自己的 fallback 路徑會往前多取 150 日暖機。
- **正確的定義或數值**：任何起日顯示的指標，都應與完整歷史的計算結果一致，或至少有足夠暖機。
- **app 實際行為**：自訂起日在 2025-11-27 到大約 2026-02 之間時，圖表開頭數十根 K 棒的 RSI 與 KD 是錯的。預設 6 個月與「1年」快捷不受這個問題影響（「1年」會走 fallback 路徑）。
- **對使用者的影響**：只有使用者手動選擇約 7～10 個月前的起日時才會出現，屬於局部體驗問題。
- **修法建議**：把快取命中條件改為 hist_min <= start − 暖機（例如 150 日曆天），不足時走既有的 warmup fallback；get_enriched_range 可比照 get_enriched_history 做暖機檢查。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:1467-1476 hist_min <= start 就直接切取快取
  - backend/app/repository.py:570 快取窗口為 latest−300 日曆天，從該日起重算指標
  - backend/app/repository.py:1461 fallback 路徑有 warmup_start = start − 150 日
  - frontend/src/components/StockPreviewDialog.tsx:234-238 DatePicker 可以自訂起日
- **線上證據**：
  - StockCharts：RSI 需要足夠的起算資料（至少 250 期）才能重現
- **本地計算證據**：
  - 快取首日為 2025-11-27。自訂起日在快取第 0 日時，首根的 rsi_6、rsi_14、rsi_24 分別差 77.5、61.6、56.4；第 10 日時 rsi_24 差 19.3、kdj_d 差 17.4；第 20 日時 rsi_24 差 14.5；第 40 日時 7.4；第 60 日時 0.70（step5_cache_edge.py 的輸出）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：finding 成立，數字全部重現。

**成因**
get_daily 只要 hist_min <= start，就直接切取記憶體快取（repository.py:1472）。而快取是從 latest−300 日（目前是 2025-11-27）起直接跑 compute_indicators，沒有暖機（repository.py:570、589）。

**影響**
使用者用 DatePicker 手動選起日時，只要起日落在快取的前約 40～60 個交易日（2025-11-27 到大約 2026-02 底），圖表開頭的 RSI 與 KD 就會錯：
- **RSI**：首根固定是 0。自選 9 檔首根的 rsi_6、rsi_14、rsi_24 最大誤差是 77.5（2454）、61.6（2454）、56.4（00885）。rsi_24 在第 10、20、40、60 根的最大誤差分別是 19.3、14.5、7.4、0.69。
- **KD**：前 8 根缺值，圖上顯示 '-'。第 9 根以後才開始算錯，第 10 根 kdj_d 誤差最大 17.4，到第 20 根已經不超過 0.7。

**範圍補充**
finding 引用的是 9 檔自選（含 4 檔 ETF）的最大值。只看 5 檔個股時，第 40 根 rsi_24 最大 4.9，第 60 根 0.48。ETF 也受影響，因為這個部署的 ETF 與指數 instruments 都是空的，ETF 會被判成 stock 而走 get_daily。

**預設快捷鍵**
- 「1年」走有 150 日暖機的 fallback 路徑，與完整歷史的誤差不超過 0.4。
- 「半年」首根誤差不超過 0.55。
- 兩者實際上都不受影響。
    - 理由：我自己寫腳本重算，沒有沿用稽核的程式。做法是照 repository._refresh_enriched 的方式重建記憶體快取：latest=2026-09-23，從 latest−300 日（即 2025-11-27）起讀 11 欄，直接跑 compute_indicators。再拿同一批資料從 2024-01-02 起的完整歷史重算一份當對照。

結論：finding 列出的每個數字都逐一重現，程式碼的位置也都對得上。

**數字怎麼來的**
- finding 的數字是自選 9 檔（5 檔個股加 4 檔 ETF）在每個偏移日的最大誤差，不是單一檔的值。
- 首根的 rsi_6 77.53 與 rsi_14 61.57 來自 2454；rsi_24 56.41 來自 00885。
- 第 10 根：rsi_24 19.32 來自 6196，kdj_d 17.40 來自 3034。
- 第 20 根：rsi_24 14.55 來自 6196。
- 第 40 根：7.38 來自 00878。
- 第 60 根：0.69 來自 0050。

**把 ETF 算進去是對的**
這個部署的 data\instruments_etf 與 data\instruments_index 都是空目錄，所以 resolve_asset_type 會把 0050 這類 ETF 判成 stock，一樣走 get_daily 的快取路徑。

**對照組不影響結論**
稽核拿 parquet 裡存的指標值當對照，我拿完整歷史重算的值當對照。兩者在 5 檔個股、2025-11-27 以後的最大差都是 0.000，所以結論與對照組的選擇無關。

**成因**
快取首日的 RSI 恰好是 0.0：首日 diff 為 null，gain 與 loss 都是 0，算出來就是 0。KD 則因為 9 日 rolling 的關係，前 8 根是 null。這證實快取是從 hist_min 開始從零計算的。

**預設快捷鍵的情況**
- 「1年」的起日是 2025-09-24，早於 hist_min，所以走 fallback 的 150 日暖機路徑。這條路徑在 hist_min 當天與完整歷史的最大差只有 rsi_24 0.384。
- 「半年」的起日是 2026-03-24，走快取路徑。9 檔首根的最大差是 rsi_24 0.55（00885），KD 為 0。所以說預設區間不受影響，基本上成立。

**需要補正的細節**
- 首根的 KD 不是「失真」，而是前 8 根缺值，圖上顯示為 '-'。KD 的錯誤集中在第 9 到約 20 根，第 10 根 kdj_d 誤差最大 17.4，到第 20 根已經不超過 0.7。
- RSI 首根固定為 0，其中 rsi_24 收斂最慢，到約第 40 到 60 根才降到 1 以下。

**嚴重度**
問題只出現在手動選的起日約落在 7 到 10 個月前（約 3 個月寬的窗口），而且只影響顯示，所以維持 P3。
    - 證據：
      - 程式碼：backend/app/repository.py:570 `start_full = latest - timedelta(days=300)`；:589 `df_full = compute_indicators(df_hist)`，df_hist 只含 date>=start_full 的資料，沒有暖機；:627 `_enriched_history_start = df_full["date"].min()`
      - 程式碼：backend/app/repository.py:1472 `if hist_min is not None and hist_min <= start and hist_max >= start:`，符合就直接切快取；:1461 fallback 路徑用 `warmup_start = start - timedelta(days=150)`
      - 程式碼：frontend/src/components/StockPreviewDialog.tsx:43-46 快捷鍵只有半年與 1年；:233-237 起日 DatePicker 只設 max，沒有下限
      - 程式碼：backend/app/repository.py:1338-1348 resolve_asset_type 用 get_etf_symbol_set 判斷；data\instruments_etf、data\instruments_index、data\instruments 都是空目錄（ls -la），所以 0050、00878、00885、00893 會被判成 stock 並走 get_daily
      - 本地計算 repro.py（AUDIT_SCRATCH/work/verify-rsikd-cachewarm/out.txt）：partitions 661 個，2024-01-02 到 2026-09-23；latest 2026-09-23；start_full 與 cache hist_min 都是 2025-11-27；快取有 200 個交易日
      - 首根的快取值與完整歷史值（rsi_6 / rsi_14 / rsi_24 / kdj_k）：2454 快取為 0.0/0.0/0.0/null，完整歷史為 77.53/61.57/54.86/61.89；2379 快取為 0/0/0，完整歷史為 77.13/57.70/51.30
      - 數字並列（本次重算 9 檔的最大值 vs finding）：第 0 根 rsi_6 77.53 vs 77.5（2454）、rsi_14 61.57 vs 61.6（2454）、rsi_24 56.41 vs 56.4（00885）；第 10 根 rsi_24 19.32 vs 19.3（6196）、kdj_d 17.40 vs 17.4（3034）；第 20 根 rsi_24 14.55 vs 14.5（6196）；第 40 根 rsi_24 7.38 vs 7.4（00878）；第 60 根 rsi_24 0.69 vs 0.70（0050）。全部吻合（repro2.py 的 C 組、repro3.py）
      - 只算 5 檔個股的最大值（repro2.py 的 A 組）：第 0 根 77.53/61.57/54.86；第 40 根 rsi_24 4.89（3545）；第 60 根 0.48（2379）。finding 的 56.4、7.4、0.70 都來自 ETF
      - 對照組是否影響結論：parquet 存值與完整歷史重算的值，在 5 檔、2025-11-27 以後的最大差是 kdj_k/kdj_d/rsi_6/rsi_14/rsi_24 全部 0.000（out2.txt），所以選哪個當對照，結論都相同
      - 全市場（2198～2254 檔）的中位誤差：第 0 根 rsi_6 55.49、rsi_24 48.55；第 10 根 kdj_d 11.73、rsi_24 10.32；第 40 根 rsi_24 2.09；第 60 根 rsi_24 0.46（out.txt）
      - KD 缺值：9 檔在快取前 10 根中，kdj_k 都有 8 根是 null，RSI 沒有 null（out3.txt）。首根 RSI 恰好等於 0.0，原因是 pipeline.py:529-548 在首日 diff 為 null 時把 gain 與 loss 都設為 0
      - 快捷鍵：『1年』起日 2025-09-24 早於 hist_min，走 fallback。fallback 在 2025-11-27 與完整歷史的最大差是 rsi_24 0.384、rsi_14 0.018，KD 為 0。『半年』起日 2026-03-24 走快取，9 檔首根最大差是 rsi_24 0.55（00885）、rsi_14 0.07，KD 為 0（out.txt、out3.txt）
      - 執行前後 git status 相同，都只有既有的 3 個修改檔，repo 沒有新增改動。腳本放在 scratchpad\audit\work\verify-rsikd-cachewarm\（repro.py、repro2.py、repro3.py 及 out*.txt）

### [P3 → P3] 自選頁台股列的 KDJ、RSI6、RSI24 永遠為「—」，且 RSI14 在盤中是前一交易日的值（確認成立）

- **主張**：自選頁的台股列只由 enrich_taiwan_watchlist_rows 產生，只計算 rsi_14，而且只用 TaiwanDailyStore（盤後才寫入）計算。前端提供的 KDJ-K/D/J、RSI6、RSI24 欄位被 setdefault 補成 None；然而 kline_daily_enriched 其實已經有這些台股的值。
- **正確的定義或數值**：台股列可以取得 enriched 已經算好的 KDJ 與 RSI6/24；盤中的 RSI 口徑應與同一列的即時價格一致，或明確標示為前一日的值。
- **app 實際行為**：這些欄位開啟後，9 檔全部顯示「—」；RSI14 在盤中落後一天，而且沒有任何標示。
- **對使用者的影響**：使用者若開啟 KDJ 或 RSI6/24 欄位只會看到「—」；盤中比對自選頁與 K 線活蠟燭時，RSI14 可能相差數點。
- **修法建議**：台股列從 repo 的 enriched 最新列（盤中為 get_enriched_today）補上 kdj_*、rsi_*，或至少在前端標示「前一交易日」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/watchlist_enrichment.py:19-23 註明 KDJ 等欄位刻意不提供
  - backend/app/taiwan/watchlist_enrichment.py:247-266 只寫入 rsi_14
  - backend/app/api/watchlist.py:457-462 其餘欄位 setdefault(None)
  - frontend/src/lib/watchlist-columns.ts:48-56 提供 RSI6、RSI24 與 KDJ-K/D/J 欄位
  - backend/app/taiwan/daily_refresh.py:175,288 是台股日K唯一的寫入者（盤後）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 的 enriched 分區已有 9 檔的 kdj_k、kdj_d、kdj_j、rsi_6、rsi_24，例如 2454 的 K 為 68.32、RSI6 為 82.52
  - 盤中時自選頁的 RSI14 仍是前一日的值：以 9/22 對比 9/23 為例，3034 為 56.95 對 51.54，00878 為 72.10 對 69.26，而同一列的價格是即時的
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：自選頁的台股列只由 enrich_taiwan_watchlist_rows 產生。它的指標只來自 TaiwanDailyStore 最近 150 個交易日（每天 16:30 盤後才寫入），而且只輸出 MA、量MA、RSI14、MACD、動量，不含 KDJ 與 RSI6/24。其餘欄位在 watchlist.py:460-464 被 setdefault 補成 None，所以使用者開啟 KDJ-K/D/J、RSI6、RSI24 欄位時，9 檔全部顯示「—」。然而 kline_daily_enriched 已經有這 9 檔的正確值，並已用官方 STOCK_DAY 獨立驗證，例如 2454 在 9/23 的 K 為 68.32、RSI6 為 82.52。此外，從開盤一直到當日 16:30 盤後更新之前，自選頁所有台股技術指標都是前一交易日的值（RSI14 是其中之一，MA、MACD、動量也一樣），同一列的價格卻是即時價，而且畫面沒有任何日期標示。以 9/22 對比 9/23 為例，RSI14 的差距為：3034 56.95 對 51.54，00878 72.10 對 69.26，6196 57.38 對 61.47，3545 57.90 對 54.14。同一時段 K 線活蠟燭的指標是用即時價算出來的，兩者可相差數點。
    - 理由：finding 裡的每一個數字都重現了，數值一致到小數第二位。

1. 自選頁 RSI14 的計算：我自己讀 TaiwanDailyStore 的 parquet，取最近 150 個分區（對應 _taiwan_indicator_map 的 available[-150:]），再呼叫純計算函式 compute_taiwan_daily_indicators。
   - 截至 9/22：3034 為 56.95，00878 為 72.10。
   - 截至 9/23：3034 為 51.54，00878 為 69.26。
   - 與 finding 所列數值完全相同，也等於 enriched 分區的 rsi_14。9 檔之中最大差只有 0.01（6196），來源是 150 根與全歷史的暖機長度不同。
   - 9 檔 9/23 相對 9/22 的差：3034 −5.41、6196 +4.09、3545 −3.76、00878 −2.84、00893 +1.80、0050 +1.40、2379 +1.34、00885 +0.30、2454 +0.08。「相差數點」的說法成立。

2. enriched 分區確實有值：9/22 與 9/23 兩個分區各有 9 檔自選，kdj_k/d/j、rsi_6、rsi_24 都不是 null。2454 在 9/23 的 K 為 68.315，RSI6 為 82.524，與 finding 相符。

3. 用官方資料獨立交叉驗證：我不使用 app 程式碼，自己實作 KDJ(9,3,3) 與 Wilder RSI，輸入官方 STOCK_DAY 的 8～9 月資料（38 根）。
   - 9 檔在 9/22、9/23 的 K、D、J 與 enriched 一致到小數第二位（0.01 以內）。
   - RSI6 最大差 0.13（2379）；以 38 根暖機，理論殘差權重是 (5/6)^37 ≈ 1.2e-3。
   - RSI14 與 RSI24 用 38 根暖機不足，理論殘差權重分別為 6.4e-2 與 2.1e-1，所以改用本地全歷史（655～661 根）加我自己的實作，結果與 enriched 完全一致。
   - 官方與本地 OHLC 在 8～9 月共 342 列，逐一比對 0 筆不符。
   - 結論：enriched 裡的這些值是真實且正確的，不是捏造出來的。

4. 程式碼路徑：
   - enrich_taiwan_watchlist_rows 只輸出 ma、vol_ma、rsi_14、macd、momentum 這幾組指標，不含 kdj 與 rsi_6/24。
   - watchlist.py:460-464 用 setdefault 把缺少的欄位補成 None。
   - 前端 primitives.tsx:93,95,102-104 遇到 null 時顯示「—」。
   - 所以使用者在自選頁開啟 KDJ-K/D/J、RSI6、RSI24 欄位後，9 檔全部顯示「—」。

5. 盤中落後一天（有 log 與檔案時間佐證）：
   - backend.log 顯示 9/23 的 TaiwanDailyUpdateService 在 16:30:01 開始、16:30:33 結束。
   - 9/23 日K分區的 mtime 是 2026-09-23 16:30:05，所以 9/23 盤中 TaiwanDailyStore 的最新一天是 9/22。
   - 同一段時間，quote_service 的 _flush_live_enriched 在 9/23 09:33～13:29 跑了 667 次「enriched 增量」，其中 292 次涵蓋全部 9 檔自選。K 線活蠟燭用 get_enriched_today 取得的指標，是用即時價算出來的。
   - 因此盤中自選頁的 RSI14 是前一日收盤的值，K 線活蠟燭卻是即時值。自選頁沒有任何日期標示：Watchlist.tsx 沒有顯示 as_of 或 live_date，欄位名稱只寫「RSI14」。

6. 不是重複回報：docs/code-review-2026-09-22.md 的 B4/B5/B6/B12/C1/C4/C5/N2～N5 都沒有涵蓋這一點。

finding 的小瑕疵，都不影響結論：
- setdefault 的實際行號是 watchlist.py:460-464，不是 457-462。
- 說 daily_refresh 是「唯一寫入者」略為武斷：bootstrap.py:388 也會把資料補進日K store。不過那是補到截至前一交易日（依 resolve_target_latest_trading_date），盤中沒有任何程式會寫入當日日K，所以結論不變。
- 落後的範圍比 finding 說的大：不只 RSI14，自選頁台股列的 MA5/10/20/60、量MA、MACD、5D/20D 動量也都是前一交易日的值；而且落後會一路持續到 16:30 盤後更新之後，不只在盤中時段。

嚴重度維持 P3。這是局部體驗問題：顯示「—」並不是錯誤的數值，而 RSI14 是真實的前一日值，只是沒有標示日期。另外 watchlist_enrichment.py:20-23 明寫這是刻意不提供，但那時還沒有台股 enriched；現在 enriched_refresh 已經算好這些值，當初的理由已經過時。
    - 證據：
      - 本地計算（自選頁 RSI14 的路徑）：AUDIT_SCRATCH/work/verify-kdj-watchlist/repro.py → 截至 9/22 與 9/23：3034 為 56.95→51.54，00878 為 72.10→69.26，0050 為 65.52→66.92，6196 為 57.38→61.47，3545 為 57.90→54.14；與 enriched rsi_14 的差 ≤0.01
      - 本地計算（獨立重算）：...\verify-kdj-watchlist\indep.py，輸出存於 indep_out.txt → 2454 在 9/23 的結果：官方 38 根算出 K/D/J 為 68.32/62.23/80.48、RSI6 為 82.52；enriched 為 68.32/62.23/80.48、RSI6 82.52、RSI14 72.15、RSI24 65.94；本地全歷史重算得 RSI14 72.15、RSI24 65.94
      - 本地計算：官方 twse_stock_day_202608_202609.csv 與 TaiwanDailyStore 在 8～9 月的 OHLC 共比對 342 列，mismatches: 0
      - 本地資料：data\kline_daily_enriched\date=2026-09-22 與 date=2026-09-23 的 part.parquet 各有 9 檔自選，kdj_k/d/j、rsi_6、rsi_24 都不是 null
      - backend/app/taiwan/watchlist_enrichment.py:20-23 明寫 KDJ 刻意不提供；:256-267 只寫入 ma、vol_ma、rsi_14、macd、momentum
      - backend/app/taiwan/watchlist_enrichment.py:150-160 指標只來自 TaiwanDailyStore 最近 150 個分區
      - backend/app/api/watchlist.py:460-464 用 expected_cols 加 setdefault(col, None) 補齊欄位；:261,263 的 _WATCHLIST_COLS 包含 rsi_6、rsi_24、kdj_*
      - frontend/src/lib/watchlist-columns.ts:48,50,54-56 提供 RSI6、RSI24、KDJ-K/D/J 欄位；frontend\src\components\stock-table\primitives.tsx:27,93,95,102-104 遇到 null 時顯示「—」
      - 檔案時間：data\taiwan\daily\date=2026-09-23\part.parquet 的 mtime 為 2026-09-23 16:30:05 +0800；backend.log 記錄『2026-09-23 16:30:01 Starting TaiwanDailyUpdateService run: target=2026-09-23』
      - backend.log：9/23 09:33～13:29 共有 667 次『enriched 增量』，其中 292 次涵蓋 9 檔；K 線活蠟燭由 backend/app/api/kline.py:514-584 經 get_enriched_today 注入 kdj、rsi_6/14/24
      - backend/app/jobs/daily_pipeline.py:180 的台股日更新排程為 mon-fri 16:30 Asia/Taipei

### [P1 → P1] 台股 KD／RSI 以未還原分割的價格計算，0050 一拆四後出現長達數月的假超賣（部分成立）

- **主張**：台股的 enriched 管線與 K 線 fallback 在計算時都沒有套用任何還原因子。0050 在 2025-06-18 一拆四，收盤價從 188.65 變成 47.57，被當成單日下跌 75%，KD 與 RSI 因此嚴重失真。
- **正確的定義或數值**：技術指標應使用還原分割（前復權）的價格，這也是 CONTRIBUTING §3.2 的規定，TradingView 同樣如此，分割日不會產生虛假的漲跌。
- **app 實際行為**：台股的 close、high、low 就是原始價；分割缺口被當成真實下跌，RSI14 連續 36 天假超賣，KD 連續約兩週接近 0。
- **對使用者的影響**：0050 在使用者的自選清單內。現在打開「1年」K 線，前約兩個月的 RSI 與 KD 是錯的，今天的值不受影響。回測期間若涵蓋 2025 年 6～8 月，RSI14<30 的超賣策略（oversold_bounce、oversold_reversal）會對 0050 產生假訊號。其他有分割或減資的台股也適用同一機制。
- **修法建議**：為台股建立分割與減資的還原因子（例如由 TWT49U 或減資、分割公告轉成 adj_factor），傳給 compute_enriched 的 factors 參數，讓 enriched 的 OHLC 符合 §3.2 的前復權契約。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
  - backend/app/api/kline.py:435 enriched = compute_enriched(local, factors=pl.DataFrame())
  - CONTRIBUTING.md:100 「enriched 的 open/high/low/close 為前復權價格」
  - backend/app/indicators/pipeline.py:528-549（RSI 直接對 close.diff() 做平滑）
- **線上證據**：
  - TradingView scanner 的 0050 RSI(14)：9/23 為 66.92025877134358、9/22 為 65.51991613103013。這兩個值與「分割還原後」的 Wilder RSI 完全相等（差 0.0），與 app 未還原的值差 6.24e-8，可見 TradingView 有還原分割（https://scanner.tradingview.com/taiwan/scan）
- **本地計算證據**：
  - data\taiwan\daily 中 0050：2025-06-10 收 188.65，2025-06-11～06-17 無資料，2025-06-18 收 47.57（step3.out）
  - 已存值與分割還原後的獨立計算比較：6/18 的 RSI6 為 5.66 對 84.69、RSI14 為 10.70 對 70.58、RSI24 為 15.76 對 62.04；6/27 的 K 為 4.38 對 90.45
  - 2025-06-18～11-28 之間：已存值 RSI14<30 有 36 天（最後一天是 2025-08-06），還原後 0 天；RSI24<30 有 58 天，還原後 0 天；RSI24 最大偏差 49.99 點
  - K 線「1年」快捷的起點 2025-09-24：已存 RSI24 為 45.99，還原後 75.70；RSI14 為 72.16，還原後 81.67
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股的 enriched 發布（enriched_refresh.py:95）與 K 線 fallback（kline.py:435）都以空的 factors 呼叫 compute_enriched，所以 kline_daily_enriched 存的是未還原的原始價（close 與 raw_close 相同）。K 線主路徑（repository.get_daily → _compute_enriched_range）與回測（engine.py:616）也從這份原始價重算指標，違反 CONTRIBUTING §3.2 的前復權契約。

0050 在 2025-06-18 一拆四（6/10 收 188.65 → 6/18 收 47.57），RSI 與 KD 因此把分割當成單日下跌 75%：
- 6/18：RSI14 10.70 對還原後 70.58。
- 6/27：K 4.38 對還原後 90.45。
- 2025-06-18～11-28：RSI14<30 有 36 天，還原後 0 天。
- 以已存欄位模擬 oversold_bounce 的進場條件，出現 7 次假進場，還原後 0 次。

在使用者的「1年」K 線視窗（起點 2025-09-24）中，KD 與 RSI6 不受影響。受影響的是：
- RSI14：最大差 9.5 點，差距 >1 持續到 2025-11 初。
- RSI24：最大差 30 點，差距 >1 持續到 2026-01 底。

0050 今天的值不受影響。同一機制也影響其他分割與減資個股：資料中有 25 檔出現單日跌幅 >40%、18 檔出現單日漲幅 >100%。其中近期分割的個股（例如推測 1 拆 3 的 6669，2026-09-02）連今天的 RSI 都失真：RSI14 25.3，還原後約 42.5。
    - 理由：核心主張成立：台股的 enriched 發布與 K 線 fallback 在呼叫 compute_enriched 時都傳入空的 factors，所以 _apply_adj_factor 直接回傳原始價，enriched 的 close 與 raw_close 完全相同。這違反 CONTRIBUTING §3.2「enriched 的 OHLC 為前復權價格」的規定。repo 裡沒有任何台股還原機制或設計文件說明這是刻意的取捨（docs 與 git log 都查不到）。定義面：StockCharts 官方說明，歷史價格要依分割比例調整，否則「most of the technical indicators on that chart would give sell signals」。另外查到 0050 確實在 2025-06-18 一拆四（前一交易日 6/10 收 188.65）。

我用自己寫的 Wilder RSI／KD 重算未還原的序列，與已存值的差距在 1e-14 以內；把 6/18 以前的 OHLC 除以 4 之後重算，得到的數字與 finding 列出的完全一致。

在使用者情境下確實會發生。0050 在自選清單內，而這個部署沒有 ETF instruments，所以 0050 走 stock 路徑，從 kline_daily_enriched 讀原始價即時重算。「1年」視窗的起點是 2025-09-24，預熱從 150 天前開始，涵蓋了分割日。回測（engine.py:616）也讀同一份原始價。

需要修正的次要不精確處：
(1) K 線主路徑是 repo.get_daily → _compute_enriched_range，不是 kline.py:435 的 fallback；fallback 只在 enriched 缺歷史時才會走到，不過兩條路徑都沒有還原。
(2)「1年」視窗裡 KD 與 RSI6 不受影響：最大差距分別為 6.9e-10 與 0.014，受影響的是 RSI14 與 RSI24。RSI14 最大差 9.46，差距 >1 持續到 2025-11-03；RSI24 最大差 30.4，差距 >1 持續到 2026-01-29。所以「前約兩個月的 RSI 與 KD」應改為「RSI14 約 1.5 個月、RSI24 約 4 個月」。
(3)「KD 連續約兩週接近 0」偏誇大：已存 K<20 只有 6/23～6/27 共 5 個交易日，K 差距 >1 持續到 2025-07-11。

原記錄沒提到的新面向：
- 資料中有 25 檔出現單日跌幅 >40%、18 檔出現單日漲幅 >100%，屬於分割或減資類缺口。
- 6669 在 2026-09-02 的缺口比為 2.99。我推測是 1 拆 3，尚未查證公司行為。它今天的已存 RSI14 是 25.33，假設因子為 3 還原後約 42.47，代表近期有分割的個股，連今天的選股與監控值都是錯的。
- 分割日成交量沒有還原，所以 vol_ratio_5d 在 6/18 高達 16.3。以已存欄位模擬 oversold_bounce 的進場條件，2025-06-18～08-31 有 7 次假進場；還原後 RSI14 從未低於 30，進場為 0 次。

嚴重度維持 P1，屬於錯誤的金融口徑：技術指標使用了未還原的價格，違反專案的復權契約。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)（行號與 finding 相同）
      - backend/app/api/kline.py:435 enriched = compute_enriched(local, factors=pl.DataFrame())；kline.py:430 的註解卻寫「已含前复权 OHLCV」
      - backend/app/indicators/pipeline.py:1024-1033：只有 factors 非空才呼叫 _apply_adj_factor，而 _apply_adj_factor 在 pipeline.py:248-249 遇到空 factors 直接 return raw
      - backend/app/indicators/pipeline.py:529-548：RSI 對 close.diff() 做 ewm(alpha=1/n)；KD 在 pipeline.py:418-478 以 9 日 high/low 計算 RSV
      - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格。」
      - backend/app/repository.py:1478-1482 與 1713-1733：「1年」查詢的起點 2025-09-24 早於 300 天歷史快取的起點（約 2025-11-27，見 repository.py:569），因此改從 kline_daily_enriched 讀 150 天預熱後以 compute_indicators 即時重算，用的仍是原始價；data\instruments_etf 為空，0050 以 stock 路徑處理
      - backend/app/backtest/engine.py:616-619：回測讀 kline_daily_enriched（台股為原始價）；oversold_bounce 的條件見 backend\app\strategy\builtin\oversold_bounce.py:68-74（rsi_14<30、收陽、vol_ratio_5d>=1.2）
      - 本地資料 data\taiwan\daily 的 0050：2025-06-10 收 188.65，06-11～06-17 無資料，2025-06-18 開 47.5、收 47.57，成交量 2.526e8；kline_daily_enriched 的 close 與 raw_close 完全相同（check.py 輸出）
      - calc.py（scratchpad\audit\work\verify-kdrsi-split\）：自寫 Wilder RSI／KD 與已存值的最大誤差為 RSI14 2.1e-14、K 1.4e-14。6/18 以前除以 4 還原後：6/18 RSI6 5.66 對 84.69、RSI14 10.70 對 70.58、RSI24 15.76 對 62.04；6/27 K 4.38 對 90.45；2025-06-18～11-28 已存 RSI14<30 共 36 天（最後一天 2025-08-06），還原後 0 天；RSI24<30 共 58 天，還原後 0 天；RSI24 最大差 49.99；2025-09-24 RSI24 45.99 對 75.70、RSI14 72.16 對 81.67；2026-09-23 RSI14 兩者皆為 66.920259
      - 「1年」路徑模擬（預熱從 2025-04-27 開始）：可見區間 RSI14 最大差 9.46，差距 >1 持續到 2025-11-03；RSI24 最大差 30.45，差距 >1 持續到 2026-01-29；RSI6 最大差 0.014；kdj_k 最大差 6.9e-10（KD 在 1 年視窗內不受影響）
      - gaps.py：全市場 2346 檔中，單日跌幅 >40% 有 28 筆（25 檔），單日漲幅 >100% 有 18 筆（18 檔）。自選 9 檔中只有 0050 有分割缺口（最小報酬 -0.748）
      - recent.py（推測因子為 3，公司行為未查證）：6669.TWSE 在 2026-09-01 收 7800、2026-09-02 收 2610；2026-09-23 已存 RSI14 25.33 對還原後 42.47，RSI24 31.28 對 49.15，今天的值仍失真
      - 以已存欄位模擬 oversold_bounce 進場條件：2025-06-18～08-31 共 7 次（6/18、7/15、7/16、7/17、7/18、7/30、8/01），6/18 的 vol_ratio_5d 為 16.32；還原後 RSI14 從未低於 30
      - 線上來源 https://help.stockcharts.com/data-and-ticker-symbols/data-availability/price-data-adjustments：「If a stock splits 2-for-1, the price is suddenly half of what it used to be, creating a large gap down on the chart」「most of the technical indicators on that chart would give sell signals」；調整方式為 divide all of the historical prices by the split ratio
      - 線上來源 https://www.bnext.com.tw/article/82332/0050-etf-split：「6月10日為分割前最後交易日，當時收盤價為188.65元」、1拆4、6/11～6/17 暫停交易，「於6月18日恢復交易…開盤價來到47.5元」
      - git status 與 session 開始時相同（只有 3 個既有的修改），本次驗證沒有寫入 repo
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：台股的 enriched 管線（enriched_refresh.py:95）與 K 線的兩條計算路徑都沒有套用還原因子，因此 enriched 的 close 等於 raw_close。這兩條路徑是 repository.get_daily 的 150 日暖機重算，以及 kline.py:435 的 fallback。0050 在 2025-06-18 一拆四（188.65→47.57，被當成 -74.8% 的單日下跌），導致 RSI 與 KD 嚴重失真，違反 CONTRIBUTING §3.2 的前復權契約。

RSI 的失真：6/18 RSI14 是 10.70，還原後應為 70.58。2025-06-18 至 11-28 之間，RSI14<30 的假超賣有 36 天（到 8/06），RSI24<30 有 58 天（到 9/05），還原後兩者都是 0 天。RSI24 最大偏差 49.99 點。

KD 的失真比原 finding 說的短：9 日視窗含分割前高點的 8 個交易日（6/18～6/27）RSV 接近 0，K 值從 56 一路降到 4.38（還原值 90.45），但低於 10 只有 3 天。6/30 起 K 值恢復，偏差 ≥1 點在 7/11 後就不再出現。

「1 年」K 線（從 2025-09-24 起）的 KD 與 RSI6 不受影響。受影響的是 RSI14 與 RSI24：
- 9/24 顯示 RSI14 72.26，應為 81.67；RSI24 46.05，應為 75.70。
- RSI14 偏差 ≥1 點持續到 2025-11-03，共 25 個交易日。
- RSI24 偏差 ≥5 點持續到 11-17；≥1 點持續到 2026-01-29，共 86 個交易日。

今天的值不受影響。回測期間若涵蓋 2025 年 6～8 月，0050 會在未還原資料上產生假進場訊號：oversold_bounce 7 個、oversold_reversal 10 個，還原後兩者皆為 0。TradingView 的 0050 RSI 與分割還原後的算法逐位相同。
    - 理由：核心主張完整重現：台股 enriched 管線與 K 線計算都沒有套用還原因子。0050 在 2025-06-18 一拆四，這個缺口被當成單日 -74.8% 的下跌，KD 與 RSI 因而嚴重失真。finding 列出的每個數值，我用 app 的純函式 compute_indicators，加上一份不依賴 app 的 Wilder RSI／KD 實作，兩條路都重算過，結果一致到 1e-14。TradingView 的數值我也親自重抓一次，與分割還原後的算法逐位相同。

以下是 finding 的數值與我重算的數值並列（finding → 本次重算）：
- 6/18 RSI6：5.66 對 84.69 → 5.659302 對 84.690762
- 6/18 RSI14：10.70 對 70.58 → 10.697844 對 70.583845
- 6/18 RSI24：15.76 對 62.04 → 15.761094 對 62.041630
- 6/27 K 值：4.38 對 90.45 → 4.377525 對 90.445520
- 2025-06-18～11-28 間 RSI14<30：36 天（最後一天 8/06），還原後 0 天 → 相同
- 同期間 RSI24<30：58 天，還原後 0 天 → 相同（最後一天 2025-09-05）
- RSI24 最大偏差 49.99 → 49.9905（2025-07-18）
- 2025-09-24 已存值：RSI24 45.99 對 75.70、RSI14 72.16 對 81.67 → 45.990407 對 75.698080、72.161753 對 81.673548
- TradingView 9/23 與 9/22 的值，與還原後的算法差 0.0；與未還原的值差 6.24e-8 → 相同

以下幾點需要更正：
1. 「KD 連續約兩週接近 0」說得過頭。RSV 只在 8 個交易日（6/18～6/27）壓在 0.06～1.41 之間，原因是 9 日最高價仍含分割前的 188.9。K 值真正低於 10 只有 3 天（6/25～6/27），低於 20 只有 5 天。6/30 視窗移出分割前資料後，K 立刻回到 34.9；偏差 ≥1 點最後一天是 2025-07-11。
2. user_impact 說「現在打開 1 年 K 線，前約兩個月的 RSI 與 KD 是錯的」，其中 KD 的部分是錯的。1 年視窗從 2025-09-24 開始，這時 KD 的失真早已結束：視窗內 KD 與還原值的最大差是 0.0000，RSI6 最大差也只有 0.014。真正錯的只有 RSI14 與 RSI24：
   - RSI14：偏差 ≥1 點共 25 個交易日（最後一天 2025-11-03），≥5 點共 7 天
   - RSI24：偏差 ≥1 點共 86 個交易日（最後一天 2026-01-29，約 4 個月），≥5 點共 35 天（最後一天 2025-11-17）
3. 0050 在 1 年視圖實際走的路徑，不是 finding 引用的 kline.py:435 fallback，而是 repository.get_daily 的即時重算。這條路徑用 kline_daily_enriched 的未還原 close，往前多抓 150 個日曆日暖機（從 2025-04-27 起）重算；歷史快取從 latest-300 天開始，蓋不到 9/24，所以用不上。畫面實際顯示的 9/24 值是 RSI24 46.05、RSI14 72.26，不是已存的 45.99／72.16。根因相同，只是顯示數值略有差異。
4. 回測假訊號的說法成立。分割後到 8 月初，oversold_bounce 在未還原資料上有 7 個進場點（6/18 至 8/01），oversold_reversal 有 10 個（6/24 至 8/05）；改用還原後的資料兩者都是 0 個。

嚴重度：違反 CONTRIBUTING §3.2「enriched OHLC 為前復權」的契約，屬於錯誤的金融口徑，而且會在回測產生假進場訊號，維持 P1。今天的即時數值不受影響，9/23 的 RSI24 只差 0.0005。

另外兩點我沒有驗證：「其他分割或減資的台股也適用」是依程式碼推論，沒有逐檔驗證。回測跨越分割日時，報酬率也可能被算成 -75%，這點我沒有查，屬於推測。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95：`compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`，傳入空的還原因子。
      - backend/app/api/kline.py:435：`compute_enriched(local, factors=pl.DataFrame())`，同樣傳入空的還原因子。
      - backend/app/indicators/pipeline.py:1023-1024 只在 factors 非空時才呼叫 _apply_adj_factor。RSI 在 pipeline.py:528-549，對 close.diff() 做 Wilder 平滑；KDJ 在 pipeline.py:463-480，以 9 日高低點計算 RSV。
      - CONTRIBUTING.md:100：「enriched 的 `open/high/low/close` 為前復權價格。」
      - 1 年 K 線的實際路徑：backend/app/repository.py:1461 設定 `warmup_start = start - timedelta(days=150)`；:1469-1481 在歷史快取蓋不到 start 時，改走 `_scan_daily_symbol` 加 `_compute_enriched_range` 重算；:569 的快取從 `latest - 300 天` 開始，也就是 2025-11-27，晚於 2025-09-24。前端依據：frontend/src/components/StockPreviewDialog.tsx:45 定義 `{ label: '1年', months: 12 }`，:211 以 setMonth 往回推 12 個月作為 start。
      - 回測策略：backend/app/strategy/builtin/oversold_bounce.py 以 `rsi_14 < 30`、收陽、量比 ≥1.2 為進場條件；oversold_reversal.py 以 `rsi_14 < 30`、漲幅 >1%、收盤價站上 MA5 為進場條件。backend/app/backtest/matrix.py:3943-3965 用 market.close 計算 RSI。repository.py:43 把 stock 對應到 kline_daily_enriched。回測是否確實讀取這個表屬於「有依據的推論」，我沒有實跑回測。
      - 本地資料：`uv run python verify-split0050/repro.py`，輸出見 repro.out。data\taiwan\daily 中 0050 共 656 列，範圍 2024-01-02 至 2026-09-23：2025-06-10 收 188.65，06-11 至 06-17 無資料，06-18 收 47.57。kline_daily_enriched 中 0050 所有列都是 close==raw_close（True），代表完全沒有還原。
      - 重現一致性：已存值與 app 未還原重算的最大差都是 0.0（rsi_6、rsi_14、rsi_24、kdj_k），與我獨立實作的 Wilder RSI／KD 最大差 ≤2.1e-14。還原算法用分割日前的 OHLC 除以 4 後重算，app 結果與獨立實作最大差 ≤2.1e-14。
      - repro.out 的關鍵值（已存 對 還原）：6/18 RSI6 5.659302 對 84.690762、RSI14 10.697844 對 70.583845、RSI24 15.761094 對 62.041630；6/27 K 值 4.377525 對 90.445520。2025-06-18 至 11-28：RSI14<30 已存 36 天（最後一天 2025-08-06），還原後 0 天；RSI24<30 已存 58 天（最後一天 2025-09-05），還原後 0 天。最大偏差：RSI24 49.9905（7/18）、RSI14 62.1949（7/03）、K 值 86.0680（6/27）。
      - KD 細節（repro.out 與 view_and_signals.out）：6/18 至 6/27 的 9 日最高價 hn9=188.9，RSV 介於 0.056～1.41；K 值低於 10 只有 3 天（6/25 至 6/27），6/30 回升到 34.87；偏差 ≥1 點最後一天是 2025-07-11。
      - 1 年視圖實際顯示值（view_and_signals.out，路徑同 repository.get_daily，資料從 2025-04-28 起重算），格式為「顯示值 對 全史還原值」：2025-09-24 RSI14 72.256062 對 81.673548，RSI24 46.049211 對 75.698080，K 值 92.083011 對 92.083011。整個視窗內 kdj_k／d／j 的最大差 0.0000，rsi_6 0.0138；rsi_14 偏差 ≥1 點 25 天（最後一天 2025-11-03），≥5 點 7 天；rsi_24 偏差 ≥1 點 86 天（最後一天 2026-01-29），≥5 點 35 天（最後一天 2025-11-17）。
      - 回測進場點（view_and_signals.out，以 pipeline 指標模擬進場條件）：oversold_bounce 未還原資料進場日為 6/18、7/15、7/16、7/17、7/18、7/30、8/01，還原後無；oversold_reversal 未還原資料進場日為 6/24、6/25、7/03、7/09、7/10、7/15、7/18、7/30、7/31、8/05，還原後無。
      - TradingView 線上值（POST https://scanner.tradingview.com/taiwan/scan，2026-09-24T07:02 抓取，輸出見 tv.out）：close 112.45，RSI 66.92025877134358，RSI[1] 65.51991613103013，RSI[2] 64.35219518206199。本地還原算法的 9/23 為 66.92025877134358、9/22 為 65.51991613103013，完全相同；未還原為 66.92025870895748，差 -6.24e-8。
      - 官方參考資料：reference/csv/twse_stock_day_202608_202609.csv 中 0050 共 38 個交易日，與本地 close 最大差 0.0，確認本地原始價正確。
      - 分割事實：https://wealth.businessweekly.com.tw/GArticle.aspx?id=ARTL003012773，標題為「0050分割「1拆4」拆股完成、6/18恢復交易」。搜尋摘要另載：6/10 為分割前最後交易日，6/11～6/17 暫停交易，分割前價格約 188.65 元，分割後參考價約 47.16 元。
      - 腳本與輸出都放在 AUDIT_SCRATCH/work/verify-split0050/，包括 repro.py／.out、view_and_signals.py／.out、tv.py／tv.out、refcheck.py、merged_0050.parquet。repo 的 git status 與開始時相同（3 個既有修改），我沒有寫入 repo 或 data。

### [P2 → P2] pipeline 的 RSI／KD 沒有暖機門檻：上市首日 RSI 一律為 0，KD 也沒有台灣慣例的初值 50（部分成立）

- **主張**：全量管線對每個標的的第一根 K 棒，以漲跌 0 作為 EWM 種子，且 avg_loss 為 0 時分母改成 1e-12，所以上市首日 RSI 會是 0（極度超賣）。之後的前 n 根 K 棒也照常輸出未收斂的值。KD 以第一個 RSV 當初值，而不是 50。同一個 repo 的 technical_indicators.py 明文規定歷史不足時要回傳 null（至少 15 根才輸出），pipeline 沒有做到。
- **正確的定義或數值**：歷史不足時 RSI 與 KD 應為 null（例如 RSI 至少 n＋1 根收盤價），或依台灣慣例以 50 起算 KD，不應產生假的 0 或 100。
- **app 實際行為**：上市首日 RSI＝0；前 2～3 週的 RSI 與 KD 由殘缺資料推得，並寫入 enriched，供圖表、監控、自訂訊號與策略使用。
- **對使用者的影響**：使用者目前 9 檔自選股的歷史都很長，今天不受影響。若加入新上市的 ETF 或股票，或建立預設的 rsi_14<30 監控規則，上市首日就會誤觸發；K 線開頭也會顯示 RSI 0 與未收斂的 KD。回測則由至少 120 日的暖機大幅稀釋，影響較小。
- **修法建議**：在 compute_indicators 內，對 RSI 按 bar 數設門檻（少於 n＋1 根時回傳 null），KD 在少於 9 根時回傳 null，並以 K0＝D0＝50 起算；增量路徑與矩陣路徑同步修改。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:530-535 第一列 diff 為 null，被 otherwise(0.0) 變成 gain=loss=0
  - backend/app/indicators/pipeline.py:541-548 沒有 min_samples 或 bar 數門檻；avg_loss==0 時分母改為 1e-12，因 avg_gain＝0 得出 RSI＝0
  - backend/app/indicators/pipeline.py:470,474 ewm_mean 以第一個非 null 的 RSV 作為種子（沒有 50）
  - backend/app/taiwan/technical_indicators.py:31-34,41 「Insufficient history -> null, never 0」，MIN_BARS_RSI_14 = 15
  - frontend/src/components/monitor/RuleEditor.tsx:294 監控規則預設為 rsi_14 < 30
- **線上證據**：
  - MoneyDJ KD 條目：「在求第一個K值與 D值時，由於沒有前一期的K值與D值，所以都以50代入」
  - StockCharts：RSI 首值為前 14 期漲跌的簡單平均，意即需要 14 期資料才有第一個 RSI
- **本地計算證據**：
  - 2024-01-02 之後才上市的 329 檔，首根 K 棒的 rsi_14 全部為 0.0；例如 00400A 在 2026-04-09 的 rsi_6、rsi_14、rsi_24 都是 0，次日都是 100（step3.out）
  - 在上市前 14 根 K 棒中，有 1790 列 rsi_14<30、875 列 rsi_14>70，母數為 4588 列
  - 00400A 在第 8 根的 K 值＝D 值＝94.77；若依台灣慣例以初值 50 起算，K 約 64.9、D 約 55.0
  - 2454 在 2024-01-12（資料首個 KD 值）：app 的 K 為 10.10、D 為 10.10，台灣慣例為 36.70 與 45.57（step1.out）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：全量管線 compute_indicators 的 RSI 沒有暖機門檻。每個標的第一根 K 棒因 diff 為 null 而被當成漲跌 0，加上分母改成 1e-12，rsi_6、rsi_14、rsi_24 一律寫成 0.0。之後在 n 根以內，RSI 由少於 n 筆漲跌算出，照常輸出，只要期間全跌或全漲就得到 0 或 100。這些值寫進台股的 enriched（新上市 329 檔，首根全為 0），供 K 線、自訂訊號、監控與策略使用，與 repo 內 technical_indicators.py「不足 15 根回傳 null」的契約不一致。依 Wilder 定義，要先有 n 期簡單平均才有第一個 RSI。矩陣回測路徑有同樣的問題。

KD 已經有 9 根的 null 門檻（rolling_min/max(9)）。問題只在以第一個 RSV 當種子，沒有用台灣常見的 K0=D0=50，屬慣例差異，差距約 2～3 週內衰減到可忽略，不是定義錯誤。

影響面：盤中遞推路徑在上市首日沒有狀態，RSI 為 null。假的 0 出現在首日收盤後的 enriched，以及第二個交易日起（平盤或下跌時）。回測暖機是整體往前延伸日曆天，對回測期間中途上市的股票沒有稀釋作用。

使用者目前監控規則 0 條，自選 9 檔都有長歷史，只有 K 線最左端與日後新上市的標的受影響。修法：RSI 在少於 n+1 根時回傳 null，全量、盤中與矩陣三條路徑同步修改。KD 是否改用 50 起算，屬於口徑選擇。
    - 理由：RSI 部分完全成立。KD 部分只算慣例差異，finding 有兩處說法過頭。

RSI：pipeline.py:531 的 diff 在每個標的第一列是 null。null>0 在 when 裡視同 false，所以 533-534 把 gain 和 loss 都設成 0.0。EWM 以 0 起算，avg_loss==0 時分母改成 1e-12（545-547），0/1e-12=0，RSI=100-100/1=0。我用 polars 1.40.1 實測過 null 的語意。另外把合成資料直接餵給純計算的 compute_indicators，第一列 rsi_6、rsi_14、rsi_24 都是 0.0。enriched 實檔：2024-01-02 之後才出現的 329 檔，首根 rsi_14 全部是 0.0；全部 2346 檔在各自第一根也都是 0。前 14 根共 4588 列，其中 rsi_14<30 有 1790 列、>70 有 875 列，與 finding 的數字相同。Wikipedia 寫明 Wilder 平滑要先用前 n 期的簡單平均初始化，所以第一根沒有任何漲跌時，RSI 依定義應該是「未定義」，不是 0。repo 自己的 technical_indicators.py:31-34、41、99-103 也把 RSI 限制在 15 根以上才輸出。結果同一檔新股，自選清單顯示 RSI 為 null，K 線與 enriched 卻是 0，口徑不一致。台灣的 enriched 每次都從整段歷史重算（enriched_refresh.py:68-95），這些假值會寫進 kline_daily_enriched，K 線 API（api/kline.py:579-581）、custom_signals、monitor、策略都會讀到。

KD：程式確實以第一個 RSV 起算，沒有用 50（pipeline.py:470、474）。實測 00400A 在 bar 8 為 K=D=94.77，台灣慣例值為 64.92／54.97；2454 在 2024-01-12 為 10.10，台灣慣例值為 36.70／45.57，數字與 finding 相符。不過 zh.wikipedia 的寫法是「可以 50% 代入」，市場先生 rich01 的留言也提到可用當日 RSV 作起點，所以這是慣例選擇，不是定義錯誤。差距以 (2/3)^n 衰減，到 bar 20 左右只剩約 0.2。另外 KD 其實已經有 9 根的門檻：rolling_min(9) 和 rolling_max(9) 預設需要滿 9 筆（pipeline.py:419、421），enriched 中 2344 檔的第一個 KD 都在 bar_idx=8。因此 finding 修法裡「KD 少於 9 根回傳 null」是現況已有的行為，「沒有 bar 數門檻」只適用於 RSI。

影響面的修正：
- 盤中遞推路徑在上市首日沒有遞推狀態，RSI 是 null，不是 0（pipeline.py:1446-1447、1562-1568）。所以監控誤觸發比較可能發生在首日收盤後寫進 enriched 的那一列，或第二個交易日起：第二天若平盤或下跌，RSI 仍會是 0。
- 回測暖機是整段資料往前延伸日曆天（strategy.py:389-390），不是逐檔門檻。回測期間中途上市的股票不會被暖機稀釋，這點 finding 寫錯了。但內建的 oversold 策略另外還要 vol_ratio_5d 或 ma5，前幾根通常不成立（推測，沒有逐行讀 valid_rolling_mean 的 kernel）。

使用者情境：監控規則 0 條，9 檔自選股都從 2024-01-02 起有長歷史，目前只有 K 線最左端（2024-01-02 起約 2～3 週）與新上市標的會看到 RSI=0 和未收斂的值。

嚴重度維持 P2：落地的 enriched 資料裡有假的 0，而且違反 repo 自己的 null 契約，屬於明確的功能錯誤。但只影響每個標的的最前段，目前對使用者的實際影響低。
    - 證據：
      - backend/app/indicators/pipeline.py:531 diff().over('symbol') → 首列 null；:533-534 when(_delta>0)...otherwise(0.0) 使首列 gain=loss=0
      - backend/app/indicators/pipeline.py:540-548 ewm_mean(adjust=False) 無 min_samples／bar 數門檻，avg_loss==0 時分母 then(1e-12)
      - backend/app/indicators/pipeline.py:419,421 rolling_min(9)/rolling_max(9) 預設需滿 9 筆 → KD 前 8 根已是 null；:470,474 K、D 以第一個非 null 的 RSV／K 為種子（非 50）
      - backend/app/taiwan/technical_indicators.py:31-34「Insufficient history -> null, never 0」、:41 MIN_BARS_RSI_14 = 15、:99-103 實際 gate
      - backend/app/taiwan/enriched_refresh.py:68-95 台股 enriched 由全段歷史 compute_enriched 重算並寫入 kline_daily_enriched
      - backend/app/backtest/matrix.py:3943-3965 矩陣路徑 RSI 同樣首根 gain=loss=0、分母 1e-12 → 0
      - backend/app/indicators/pipeline.py:1446-1447,1562-1568 盤中遞推：無歷史狀態時 _rsi_avg_* 為 null → 上市首日盤中 RSI 為 null，不是 0
      - backend/app/backtest/strategy.py:389-390 warmup_days = max(120, warmup_bars*1.6) 只是整體起日往前延伸，非逐檔門檻
      - frontend/src/components/monitor/RuleEditor.tsx:294 預設條件 rsi_14 < 30（屬實）
      - 本地計算：cd backend && uv run python ...\scratchpad\audit\work\verify_rsikd_def\check_enriched.py（輸出 check_enriched.out）→ enriched 1,423,597 列／2346 檔；首根在資料起日之後者 329 檔，首根 rsi_14 全為 0.0（329/329），rsi_6/rsi_24 亦全為 0；全部 2346/2346 檔首根 rsi_14=0；前 14 根 4588 列中 <30 有 1790、>70 有 875、null 0；第一個 KD 的 bar_idx 全為 8（2344 檔）
      - 本地計算：00400A.TWSE 2026-04-09 rsi_6/14/24=0.0，04-10 皆 100.0；bar 8（04-21）K=D=94.767，台灣慣例 K0=D0=50 算得 K=64.92、D=54.97；bar 20 時兩者差距已縮到約 0.23
      - 本地計算：2454.TWSE 2024-01-12 app K=D=10.101，慣例 K=36.700、D=45.567；2024-01-02～01-08 rsi_14 均為 0.0
      - 本地計算：合成 12 根資料直接餵 compute_indicators(needed={rsi_*,kdj_k,kdj_d}) → 首列 rsi_6/14/24=0.0；第 9 根 kdj_k=kdj_d=87.5（=首個 RSV）
      - 線上：https://en.wikipedia.org/wiki/Relative_strength_index 原文「The smoothed moving averages should be appropriately initialized with a simple moving average using the first n values in the price series.」
      - 線上：https://zh.wikipedia.org/wiki/随机指标 原文「若無前一日 K 值或 D 值，可以 50% 代入計算。」（措辭為「可以」，屬慣例）
      - 線上：https://rich01.com/what-is-kd-indicator/ 內文未規定初值；留言回覆提到可用 0.5 或當日 RSV 作起點，前期數值穩定前會與他處不同（顯示 KD 種子屬慣例選擇）
      - docs/code-review-2026-09-22.md 無此暖機／首根 RSI=0 的既有記錄（grep rsi/kdj/warm 只命中 A5/A6 的遞迴路徑差異），非重複回報
      - 工作樹比對：git status 前後相同（僅 session 開始前既有的 3 個修改檔）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：全量管線 compute_indicators 的 RSI 沒有暖機門檻（pipeline.py:530-548）。每檔標的在本地資料中的第一根 K 棒，gain 與 loss 都被設成 0，分母再改成 1e-12，因此 rsi_6、rsi_14、rsi_24 一律為 0。本地 2346 檔全部如此，其中 329 檔是 2024-01-02 之後才出現的新股（例如 00400A 的上市日 2026-04-09）。第二根只要上漲就是約 100，沒上漲就是 0。新股前 14 根共 4588 列，其中 1790 列 rsi_14<30、875 列 >70。和以 SMA 為種子的標準 Wilder RSI 相比，偏差在第 20～30 根仍有 2～10 點。

KD 其實有 9 根門檻：rolling_min(9) 與 rolling_max(9) 的 min_samples 預設等於視窗長度，所以前 8 根是 null。KD 的問題只在初值：app 以第一個 RSV 當 K0＝D0，而不是台灣慣例的 50。00400A 第 9 根（2026-04-21）的 K＝D＝94.77，依慣例應為 64.92／54.97；差距到第 30 根只剩 0.004，第 60 根已經歸零。

2454 的 KD 差異（10.10 對 36.70／45.57），是本地資料起點 2024-01-02 造成的效應，不是上市首日，也不影響今天的數值。

監控方面：上市首日盤中走增量路徑，新股沒有 live_agg 狀態時 RSI 應為 null（推測）。誤觸發 rsi_14<30 較可能發生在上市第二個交易日盤中，而且當天沒有上漲，因為 live_agg 的 RSI 狀態也以 0 為種子（repository.py:816-825）。

修法要點：RSI 應設最少 bar 數門檻，或改用 SMA 種子；KD 只需要改初值為 50，不需要另加門檻。增量路徑與 live_agg 的種子必須同步修改。另外，live_agg 只用 90 個日曆日重建 RSI 狀態（repository.py:746），使用者自選股盤中 rsi_24 與全歷史值相差最多約 1.2，屬相關的新面向。
    - 理由：finding 引用的數字，我用自己的腳本全部重現，而且完全一致。我另外用純 Python 迴圈重寫了一次 app 的公式，算出的 RSI／KD 與 enriched 存的值，最大差異只有 2.8e-14。把 00400A 的 OHLC 餵給 compute_indicators，結果和 enriched 逐位相同，所以數值確實出自 pipeline。

有三點需要修正：
(1) KD 並非「沒有暖機門檻」。_kdj_ln 與 _kdj_hn 用 rolling_min(9) 和 rolling_max(9)，min_samples 預設等於視窗長度，所以前 8 根本來就是 null，從第 9 根才開始輸出。KD 真正的問題只有初值：app 用第一個 RSV，台灣慣例用 50。這個差異第 30 根時還剩 0.004，到第 60 根已經歸零。修法建議裡「KD 少於 9 根時回傳 null」其實現在就是這樣。finding 說的「第 8 根」是 0 起算的索引，實際上是第 9 根（2026-04-21）。
(2) 首根 RSI＝0 不只發生在新上市股。本地資料的所有 2346 檔，首根都是 0；從 2024-01-02 起算的那些，屬於資料視窗的起點效應。2454 的例子也是這種起點效應，不是上市首日。
(3) 「上市首日就會誤觸發監控」的時間點推論不精確。盤中增量路徑對沒有 live_agg 狀態的新股，RSI 會是 null。由程式碼推得，未實跑：誤觸發比較可能出現在上市第二個交易日盤中，而且當天沒有上漲。原因是 live_agg 也用 0 當種子，狀態是 (0,0)。另外補充：只設 n+1 根門檻並不能消除收斂偏差。與 StockCharts 以 SMA 為種子的 Wilder RSI 比較，第 20 根差 2.0～9.6，第 30 根仍差 2.1～5.1。

finding 沒提到的相關面向：live_agg 只用 90 個日曆日（約 64 根）的歷史，種子設為 0，重建 RSI 狀態。以此重算，使用者自選股的 rsi_24 與全歷史值相差 0.03～1.23，全市場最大 14.08。這會實際影響盤中值；盤中實際走哪一條路徑沒有實跑，屬推測。

嚴重度維持 P2：這是明確的功能錯誤，但使用者目前的 9 檔自選股與 0 條監控規則都不受首根問題影響。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:530-548。第一列 diff 為 null，otherwise(0.0) 把 gain 與 loss 都變成 0；avg_loss==0 時分母改成 1e-12，因此首根 RSI＝100-100/(1+0)＝0。沒有任何 bar 數門檻。
      - 程式碼：pipeline.py:418-421 是 rolling_min(9) 與 rolling_max(9)，min_samples 用預設值，所以前 8 根的 RSV 是 null；pipeline.py:470,474 的 ewm_mean(adjust=False) 以第一個非 null 的 RSV 當種子。本地實測：新股 bar_idx 0～7 的 kdj_k 全為 null（例如 bar 7 有 328/328 列 null），bar_idx 8 起 0 個 null（s2.out）。
      - 重現數字對照（finding → 我算出的值）：2024-01-02 之後才出現的標的 329 檔 → 329 檔，首根 rsi_14 全部為 0.0（329/329），rsi_6 與 rsi_24 也都是 0；前 14 根共 4588 列 → 4588 列；rsi_14<30 有 1790 列 → 1790；rsi_14>70 有 875 列 → 875；rsi_14 為 null 的列數 0。指令：uv run python verify-rsikd/s2_stats.py，輸出在 s2.out。
      - 00400A.TWSE：2026-04-09 的 rsi_6、rsi_14、rsi_24 都是 0.0，04-10 都是 100（實際值 99.99999999）；K＝D＝94.767442 出現在 bar_idx 8，也就是第 9 根、2026-04-21。台灣慣例（K0＝D0＝50）下 K＝64.92、D＝54.97，finding 寫 64.9 與 55.0，相符。|K_app−K_tw| 在 bar 20 為 0.23、bar 30 為 0.004、bar 60 為 0（s3.out）。
      - 官方上市日：參考資料 twse_t187ap47_L_etf_meta.csv 的 00400A listing_date＝2026-04-09，與 enriched 首根相同。上市日晚於 2024-01-02 的 ETF 共 83 檔，其中 82 檔首根日期等於官方上市日（另 1 檔 00951 差 1 日），83 檔首根 rsi_14 全部為 0。
      - 2454.TWSE：2024-01-12 的 app K＝D＝10.10101，台灣慣例 K＝36.70、D＝45.57，與 finding 相符。但這是本地資料起點（2024-01-02）的效應，不是上市首日：2454 前 5 根（連 5 天收跌）的 rsi_14 都是 0。所有 2346 檔的首根 rsi_14 都是 0.0（s2.out）。
      - 我以純 Python 迴圈獨立實作的 app 公式，與 enriched 存值的最大差異：00400A 的 rsi14 為 2.13e-14、K 為 2.13e-14；2454 為 2.84e-14。把 00400A 的 OHLC 餵給 compute_indicators(needed={rsi_14,kdj_k,kdj_d})，與 enriched 的差異為 0.0（s3.out）。
      - 收斂偏差（app 的 0 種子 RSI 對 StockCharts 的 SMA 種子 Wilder RSI）：00400A 在 bar 20／30／60 分別差 2.03／2.15／0.28；2454 分別差 9.63／5.10／0.32。所以即使設 n+1 根門檻，第 15～30 根仍有數點偏差（s3.out）。
      - 監控觸發時間點：pipeline.py:1445-1447 用 left join 串 live_state，pipeline.py:1562-1568 做 RSI 遞推。新股沒有狀態時 avg_gain 為 null，RSI 也是 null，所以上市首日盤中不會是 0（推測，未實跑）。repository.py:816-825 的 live_agg 同樣以 0 為種子，第二日狀態為 (0,0)，當天只要沒上漲，RSI 就是 0。預設條件確認在 frontend\src\components\monitor\RuleEditor.tsx:294：`{ field: simple ? 'close' : 'rsi_14', op: '<', value: simple ? 0 : 30 }`。
      - 新面向：repository.py:746 設定 `start_60d = latest - timedelta(days=90)`，live_agg 的 RSI 狀態只由這段視窗、以 0 為種子重建。以 2026-06-25～09-23 共 64 根重算，自選 9 檔的 rsi_14 差 0.003～0.26、rsi_24 差 0.03～1.23（2379 為 1.23、3034 為 1.17）；全市場 1305 檔中，rsi_24 的 p95 為 2.20、最大 14.08（s4.out）。盤中實際採用哪個分支未實跑，影響程度屬推測。
      - 線上來源：MoneyDJ KD 條目 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=02e9d1fa-499f-4952-9f5b-e7cad942c97b 原文「由於沒有前一期的K值與D值，所以都以50代入」。StockCharts https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi 原文「First Average Gain = Sum of Gains over the past 14 periods / 14」，以及「SharpCharts uses at least 250 data points before the starting date of any chart」。
      - 對照組：backend/app/taiwan/technical_indicators.py:31-34,41,99-103 以 _bar_count>=15 為門檻，不足時輸出 null；該模組只供 watchlist_enrichment.py:148-159 使用，所以同一檔新股在自選表顯示 null，在 K 線或 enriched 則是 0。
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-rsikd/（s1_load.py、s2_stats.py／s2.out、s3_recompute.py／s3.out、s4_window.py／s4.out）。git status 與開始時相同，repo 未被修改。

### [P3 → P3] （潛在）RSV 分母為 0 時產生 NaN，全量 KD 之後永久變成 null，且與回測矩陣路徑的結果不一致（部分成立）

- **主張**：fill_null(1e-12) 只處理 null，無法處理 9 日最高價等於最低價造成的 0/0。產生的 NaN 經 ewm_mean 會一路傳下去，被 compute_all 清成 null，因此該標的其後所有日期的 KD 都消失。矩陣路徑則會跳過無效值並繼續計算，兩條路徑的結果因此不同。
- **正確的定義或數值**：分母為 0 時，應沿用前一日的 K/D，或令 RSV＝50，而且各路徑一致。
- **app 實際行為**：一旦出現這種情況，全量路徑的 KD 會永久失效。
- **對使用者的影響**：目前資料中沒有觸發；未來若有極冷門或平盤的標的出現 9 日零振幅，該標的在圖表、監控與選股中的 KD 會永久顯示「—」。
- **修法建議**：將 RSV 寫成 when(hn==ln).then(null)，並讓 K 延續前值（例如 ewm_mean(ignore_nulls=True)），或令 RSV＝50；增量路徑與矩陣路徑採用同一規則。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:465-470 (hn−ln).fill_null(1e-12) 不處理 0
  - backend/app/indicators/pipeline.py:1537 增量路徑也一樣
  - backend/app/indicators/pipeline.py:963-972 NaN 被轉成 null
  - backend/app/backtest/matrix.py:4092-4096 _matrix_ratio 在分母為 0 時給 NaN；valid_ewm_adjust_false（matrix.py:3387-3422）只在有效觀測值時前進
- **線上證據**：
  - MoneyDJ KD 條目沒有規定分母為 0 的處理（WebFetch 結論：「未特別說明分母為零時的處理方式」）
- **本地計算證據**：
  - 合成序列（9 根 high＝low＝11.0）餵給 compute_all：K、D、J 從平盤窗口起到序列結束全為 null；matrix 路徑則在 2 根 NaN 後恢復（99.24、99.50、66.33…）（step4.out）
  - 全庫 2346 檔、1,423,597 列中，9 日 high＝low 的情況為 0 例，目前沒有觸發（step3.out）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：KD 的 RSV 分母用 fill_null(1e-12) 處理，只能擋 null，擋不住 0。9 日最高價等於最低價（連續 9 天同價、零振幅）時，RSV＝0/0＝NaN（pipeline.py:465-470；增量路徑 1535-1541 也一樣）。這個 NaN 經 ewm_mean 或遞推傳到該根之後的每一根，影響分成三種情況：

1. 會把 NaN 清成 null 的路徑，KD 從該根起到資料結尾全是 null，而且只要平盤那根還留在計算資料裡，就不會恢復。這包括 compute_all（台股全量 enriched parquet 用完整歷史重算，enriched_refresh.py:65-95），以及盤中增量（pipeline.py:1719-1728）。
2. 只在日期窗口上跑 compute_indicators 的路徑，結果是 NaN 而不是 null，平盤區塊移出窗口後就恢復，所以不是永久的。這包括記憶體 enriched 快取（約 300 日曆天）、圖表 get_daily、live_agg 狀態與 screener。
3. 回測矩陣路徑只在 0/0 那幾根輸出 NaN，然後從前一個狀態繼續遞推，與前兩類結果不同。

重現數值：
- finding 的序列其實是連續 10 根 11.0，所以矩陣路徑有 2 根 NaN，之後為 99.24、99.49、66.33。
- 剛好 9 根平盤時，矩陣路徑只有 1 根 NaN；compute_all 的後續 121 列全為 null。

目前全庫 1,423,597 列中觸發 0 例。最接近的是 6020.TPEX，出現過連續 7 天同價零振幅。使用者自選的 9 檔不會觸發，全市場選股中的冷門上櫃股有機會觸發。這是潛在問題，屬 P3。

修法：分母為 0 時令 RSV 為 null，並讓 K/D 沿用前值（或令 RSV＝50）；全量、增量、窗口與矩陣四條路徑採用同一規則。
    - 理由：我自己寫了三支腳本，重跑合成序列與全庫掃描，finding 的核心機制和數值都重現了：
1. 9 日窗口內最高價等於最低價時，RSV 是 0/0＝NaN。fill_null(1e-12) 不會改變這個結果。
2. compute_all（全量）從該根起到序列結束，K/D/J 全是 null；只呼叫 compute_indicators 時則是 NaN。
3. 增量遞推一樣會被汙染：當天出現 0/0 會得到 NaN，清理後變成 null；隔天把 null 或 NaN 當前值遞推，結果仍是 null。
4. 矩陣路徑只在 0/0 那幾根輸出 NaN，之後從前一個狀態繼續算。兩條路徑的結果確實不同。
5. 全庫實際觸發 0 例。

矩陣路徑的數值與 finding 一致：99.2418、99.4945、66.3297。finding 寫的 99.50 是四捨五入的差異，本質一致。

需要修正的有兩處：
(a) finding 的合成序列其實是連續 10 根 11.0（尾段第一根也是 11.0），所以才會出現 2 根 NaN。剛好 9 根平盤時，矩陣路徑只有 1 根 NaN。
(b)「永久」這個說法只對兩種情況成立：從完整歷史重算的全量 enriched parquet（enriched_refresh.py 用全部歷史跑 compute_enriched→compute_all），以及把前一日 null 當狀態的遞推。user_impact 說圖表、監控與選股會「永久顯示『—』」，這言過其實。圖表（get_daily）、記憶體 enriched 快取、盤中 live_agg 狀態與 screener 都是在「日期窗口」上跑 compute_indicators：歷史快取約 300 日曆天，get_daily 的回退路徑是 start−150 天，live_agg 的回退路徑是 90 天。這些路徑的值是 NaN，不是 null（compute_indicators 不做清理），而且平盤區塊一旦移出窗口就會恢復。我用 S3 實測，窗口起點落在平盤區塊內或之後，最後一根 K＝51.357，NaN 數量是 0。NaN 在前端怎麼顯示，我沒有驗證。

危害面：全庫最長「同價零振幅」連續日是 6020.TPEX 的 7 天（兩次），距離觸發條件 9 天只差 2 天。使用者自選的 9 檔全是大型上市股，不會觸發，但全市場選股可能遇到。目前 0 例，屬於潛在問題，維持 P3。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:465-470 RSV＝100*(close-_kdj_ln)/(_kdj_hn-_kdj_ln).fill_null(1e-12)，之後 ewm_mean(alpha=1/3, adjust=False)；分母為 0 時不處理
      - 程式碼：backend/app/indicators/pipeline.py:1535-1541 增量遞推使用同一式子；pipeline.py:1719-1728 增量結果的 NaN/Inf 轉成 null
      - 程式碼：backend/app/indicators/pipeline.py:963-972 compute_all 把 NaN/Inf 轉成 null；compute_indicators 本身不做清理
      - 程式碼：backend/app/backtest/matrix.py:4092-4096 _matrix_ratio 在分母為 0 時給 NaN；matrix.py:3359-3384 _valid_ewm_kernel 遇到無效值就 continue（該位置輸出 NaN、保留狀態）；matrix.py:3920-3932 KD 採用這兩者
      - 程式碼：backend/app/taiwan/enriched_refresh.py:65-95 全量 enriched 用完整歷史跑 compute_enriched（會經過 compute_all），這條路徑的 null 才是永久的
      - 程式碼：backend/app/repository.py:568-590（記憶體快取：近 300 日曆天 compute_indicators）、repository.py:746-777（live_agg 的 kdj 狀態取自該快取）、repository.py:1461-1481（get_daily 用快取或 start−150 天重算）；這些路徑的平盤影響會隨窗口移出而消失
      - 本地計算 S1（finding 原序列）：uv run python verify-kdnan-repro\s1_synthetic.py → 第 23、24 根 hn−ln＝0.0；compute_all 從第 23 根起 17 列的 kdj_k/d/j 非 null 數＝0/0/0；compute_indicators 同範圍 NaN＝17；矩陣 kdj_k 只有第 [23,24] 根是 NaN，之後為 99.24、99.49、66.33、60.89…，與『沿用前值』參考實作完全相同
      - 本地計算 s3_nearmiss.py：S1 連續 11.0 的根數＝10（不是 9）；矩陣 kdj_k 第 25-27 根＝99.24175、99.49451、66.32967。finding 寫的 99.50 是四捨五入差異
      - 本地計算 S2（剛好 9 根平盤、之後 120 根）：第 38 根 hn−ln＝0；compute_all 從第 38 根起 121 列 KD 全為 null；矩陣只有第 [38] 根是 NaN，第 39 根恢復為 97.44
      - 本地計算 S3（窗口重算）：compute_indicators 窗口起點＝0 或 30（完整包含平盤區塊）時，最後 K＝NaN，NaN 數＝121；起點＝31、39、60 時，最後 K＝51.357，NaN 數＝0。窗口路徑會恢復
      - 本地計算 S4（增量公式）：前值 K＝40、D＝45，當天平盤 → (nan, nan)，清理後為 (None, None)；前值 None → None；前值 NaN → NaN，清理後為 None
      - 本地計算 s2_dbscan.py：taiwan/daily 661 分區、1,435,948 列；經 filter_halt_days 後為 1,423,597 列、2,346 檔。9 日 high_max＝low_min 的窗口有 0 個；stored kline_daily_enriched 共 1,423,597 列，bar≥8 的 1,404,834 列中 kdj_k/d/j 的 null、NaN、inf 都是 0。與 finding 的『0 例』一致
      - 本地計算 s3_nearmiss.py：同價零振幅連續天數最長的是 6020.TPEX，有 7 天（2024-12-24 起、2025-10-31 起各一次）；其次是 00791B、3067、6997、2740 等 TPEX 標的，各 6 天。觸發需要 9 天
      - 工作樹檢查：git status 只列出 session 開始前就有的 3 個改動檔；所有腳本與輸出都在 scratchpad\audit\work\verify-kdnan-repro\

## 確認正確的項目

- **KD 公式與參數（RSV 9 日、K＝2/3 前K＋1/3 RSV、D＝2/3 前D＋1/3 K）**：讀程式碼：backend/app/indicators/pipeline.py:418-421 用 rolling_min/max(9)，pipeline.py:463-475 用 ewm_mean(alpha=1/3, adjust=False)。另用純 Python 獨立實作台灣 KD（初值 50，見 work\A03-kd-rsi\indep.py），在完整日K歷史（2024-01-02 起）上計算，再與已存 enriched 比對。範圍是 9 檔自選股、2026-08-13～09-23，共 270 列。
  - 來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=02e9d1fa-499f-4952-9f5b-e7cad942c97b 原文：「在求第一個K值與 D值時，由於沒有前一期的K值與D值，所以都以50代入」、「一般N取9天」
  - 來源：https://www.cmoney.tw/learn/course/cmoney/topic/138（K＝昨K×2/3＋RSV×1/3）
  - 來源：本地：step1.out 顯示 max|dK|≤1.42e-14、max|dD|≤2.84e-14；9/23 的 2454 為 K 68.315191／D 62.231775，app 與獨立計算相同
- **J 值＝3K−2D**：讀程式碼 pipeline.py:476-479；與獨立計算比對後 max|dJ|≤5.7e-14。另依定義確認 J 可超過 100（例如 00893 在 9/23 的 J 為 109.61）
  - 來源：https://chainstockinsight.com/kdj-indicator-guide-2025/ 原文：「標準 J 值 = 3 × 當日 K 值 – 2 × 當日 D 值…少數傳統軟體或文獻會出現「J = 3D – 2K」的倒置定義」
  - 來源：https://homedadpro.com/kdj-indicator/（J = 3K − 2D）
- **RSI 6/14/24 的 Wilder 平滑（alpha＝1/n）**：讀程式碼 pipeline.py:528-549。獨立實作 Wilder 原始定義（首值取前 n 日漲跌的簡單平均，之後以 (前值×(n−1)＋今值)/n 遞推），在 9 檔 × 30 日上比對：rsi_6 與 rsi_14 最大差 2.8e-14，rsi_24 最大差 1.7e-11（種子差異在 660 根 K 棒後已收斂）
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi 原文：「First Average Gain = Sum of Gains over the past 14 periods / 14」「Average Gain = [(previous Average Gain) x 13 + current Gain] / 14」
  - 來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9 原文：「UP t = UP t-1 + 1 / N ( Ut – UP t-1)」（威爾德平滑法）
  - 來源：本地：step1.out
- **三方比對：RSI(14) 與 TradingView**：curl POST https://scanner.tradingview.com/taiwan/scan 取欄位 RSI 與 RSI[1]，涵蓋 9 檔自選股的 9/23 與 9/22 共 18 個值，與 kline_daily_enriched 的 rsi_14 比對。0050 以外的 8 檔差異 ≤1.4e-14；0050 差 6.24e-8。改用「分割還原後」的 0050 收盤價獨立計算時差異是 0.0，可見 TV 有還原分割、app 沒有（見 findings）
  - 來源：TradingView scanner 回傳值：2454 RSI=72.15480578471718、RSI[1]=72.07389039561748；3034 為 51.540316847420826／56.945624177569414；0050 為 66.92025877134358；00878 為 69.25542749513485；6196 為 61.46917705612299；2379 為 60.09833702231875；3545 為 54.13981064951432；00885 為 47.42942881837226；00893 為 70.05910765392113
  - 來源：app 值：2454 為 72.154806、3034 為 51.540317、0050 為 66.920259、00878 為 69.255427、6196 為 61.469177（data/kline_daily_enriched/date=2026-09-23\part.parquet）
- **指標輸入資料：台股日K的 OHLC**：data\taiwan\daily 與官方 twse_stock_day_202608_202609.csv 比對 9 檔 × 38 日，共 342 列。開高低收最大差異為 0，缺列 0。另外觀察到成交量最大差 434,469 股；KD 與 RSI 不使用成交量，因此交給量能領域處理
  - 來源：reference\csv\twse_stock_day_202608_202609.csv
  - 來源：本地：step1.out 顯示 d_o=d_h=d_l=d_c=0.0
- **盤中增量 KD（_kdj_8d_low/_kdj_8d_high 與 K/D 遞推）與全量等價**：以假的 self 呼叫 KlineRepository._build_live_agg（repository.py:927-928 用 tail(8) 的 min/max），再呼叫 pipeline.compute_enriched_today（pipeline.py:1534-1544）。把 9 月 17 個交易日各當成「今天」，與全量結果比對 153 列：kdj_k/d/j 最大差 5.7e-14；rsi_6 最大差 0.0012
  - 來源：本地：step2.out 的 (b) 段
- **自選頁台股 RSI14（technical_indicators.compute_taiwan_daily_indicators，回看 150 個分區）**：呼叫這個純函式（technical_indicators.py:89-103，alpha＝1/14，至少 15 根 K 棒才輸出），與已存值比對 9/23 的 9 檔，最大差 2.7e-4
  - 來源：backend/app/taiwan/technical_indicators.py:41
  - 來源：本地：step2.out 的 (c) 段
- **記憶體 300 日歷史快取與 K 線預設 6 個月區間**：以 repository.py:570 的 300 日窗口重跑 compute_indicators。在 8/13～9/23 區間，KD 差 0、rsi_24 最大差 0.0116；在 K 線預設起點 2026-03-24～31，rsi_24 最大差 0.548、rsi_14 最大差 0.073
  - 來源：本地：step2.out 的 (a) 段
- **K 線 fallback 路徑（起點往前取 150 日暖機，kline.py:315、repository.py:1461）**：分別以 2025-01-02、2025-06-02、2025-09-01 為起點重算。前 10 日 rsi_24 最大差 0.70、rsi_14 最大差 0.086、KD 最大差 1.3e-12
  - 來源：本地：step2.out 的 (e) 段
- **回測與內建策略使用的矩陣路徑 matrix_feature（kdj_k/d/j、rsi_n）**：讀 backend/app/backtest/matrix.py:3920-3963，並以 build_market_data_matrix 搭配 matrix_feature 對 9 檔的完整歷史計算。排除前 30 根後與已存值比對，最大差 3.6e-4（float32 精度造成）
  - 來源：本地：step4.out
- **平均跌幅為 0 且有上漲時 RSI＝100**：pipeline.py:544-548 在分母為 0 時改用 1e-12，結果趨近 100，符合 Wilder 定義（沒有下跌時 RSI 為 100）
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
- **除息缺口不還原（計算股利口徑）**：00878 於 2026-08-18 除息 1.01、2379 於 2026-09-08 除息 25，都落在比對窗口內。app 的 RSI14 仍與 TradingView 預設（不還原股利）位元級一致，代表口徑相同
  - 來源：reference\csv\twse_twt49u_20260601_20260923.csv（00878 與 2379 兩列）
  - 來源：TradingView scanner 回傳值同上
- **前端 KD／RSI 顯示元件**：讀 frontend/src/components/EChartsCandlestick.tsx:232-309：RSI 副圖 y 軸固定 0～100；KDJ 副圖自動縮放，J 超過 100 仍可顯示；數值以 toFixed(1) 顯示。stock-table\primitives.tsx:26-34 的 RSIBadge 以 80/70/30/20 分色。StockDailyKChart.tsx:80-85 把欄位原樣轉傳
  - 來源：MoneyDJ RSI 條目：「RSI指標值在20以下為超賣區」「RSI指標值在80以上為超買區」
  - 來源：StockCharts：70/30 為 Wilder 原始門檻
- **策略合理性：內建策略 oversold_bounce 與 oversold_reversal 的 RSI14<30，以及監控規則預設 rsi_14<30**：讀 backend\app\strategy\builtin\oversold_bounce.py:68-69、oversold_reversal.py:73-74、frontend\src\components\monitor\RuleEditor.tsx:294。30 是 Wilder 與 StockCharts 的超賣門檻；台灣 MoneyDJ 慣用 20。兩者都是公認口徑，屬於設計選擇，不算錯誤
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
  - 來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9
- **RSI 週期口徑（app 用 6/14/24，台灣常見 6/12 或 5/10）**：查證台灣慣例：MoneyDJ 列 6 日與 12 日；Yahoo 的文章以 RSI5 與 RSI10 舉例；14 日是 Wilder 的原始建議。app 的 RSI6 與 RSI14 可以直接和同週期的數值比對；app 沒有 5、10、12 日，這是參數口徑差異，不是計算錯誤
  - 來源：MoneyDJ RSI 條目（6 天、12 天）
  - 來源：https://tw.stock.yahoo.com/news/%E6%8A%80%E8%A1%93%E5%88%86%E6%9E%90-rsi%E6%8C%87%E6%A8%99-%E5%B8%82%E5%A0%B4%E9%81%8E%E7%86%B1-%E5%8F%83%E6%95%B8%E8%A8%AD%E5%AE%9A-rsi%E8%83%8C%E9%9B%A2-%E9%BB%83%E9%87%91%E4%BA%A4%E5%8F%89-%E6%AD%BB%E4%BA%A1%E4%BA%A4%E5%8F%89-133224210.html（搜尋摘要：RSI5 與 RSI10 的交叉）

## 無法驗證的項目

- **KD(9,3,3) 的第三方數值比對**：試過 HiStock tchart、Yahoo 技術分析、concords.moneydj zcw1、CMoney、nStock、聚財網，都由 JavaScript 動態產生，沒有數值；Goodinfo 回應 HTTP 403。TradingView 的 Stoch.K／Stoch.D 是 %K(14,3,3) 的 SMA 版本，定義與台灣 KD 不同，無法拿來比對。KD 只能用權威定義加上獨立實作來驗證。
- **搜尋摘要中的「2026-07-21 聯發科 K9＝43.67、D9＝28.33」**：WebSearch 的摘要沒有指出來源頁面，無法確認日期與口徑。app 與獨立計算在 7/21 都是 K 25.44、D 20.65，7/22 是 K 40.74、D 27.35，兩天都不相符；來源不明，無法判定誰對。
- **Yahoo 奇摩股市的 J 值公式（搜尋摘要稱其使用 3D−2K）與 RSI 預設週期**：技術分析頁是動態頁面；說明頁 https://tw.help.yahoo.com/kb/SLN35891.html 沒有寫參數與公式。
- **poorstock.com 在 2026-09-23 17:00 顯示聯發科 RSI 80.88**：頁面沒有標明週期與平滑法。本地計算 Wilder RSI(7)＝80.55、Cutler RSI(8)＝80.93，都不完全相等，無法確定其口徑。
- **除息當日盤中增量 RSI 的 delta（pipeline.py:1557 用 close−prev_close，其中 prev_close 來自即時報價的昨收）**：推測：如果 MIS 的 y 欄在除權息日給的是參考價而不是前一日收盤，盤中 delta 會不含股利缺口，而收盤後全量計算用 close.diff() 會包含，兩者當日會不一致。規則禁止連線 MIS，參考快照也不是除息日，所以無法驗證。
