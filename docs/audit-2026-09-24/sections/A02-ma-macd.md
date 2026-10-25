# A02-ma-macd｜均線、EMA 與 MACD

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 4 | 5 | 0 | 0 | 0 | 9 | 5 |

## 稽核者摘要

全量路徑（盤後 enriched、自選頁 technical_indicators.py、台股選股器）的 MA5/10/20/30/60 都是收盤價 SMA，EMA 與 MACD 用 α=2/(n+1)、參數 12/26/9。先確認店內 OHLC 與官方 STOCK_DAY 逐筆相同（380 列，差 0），再用純 Python 獨立重算並與 app 比對：10 檔、Aug–Sep 共 38 個交易日，最大差異 ≤1e-11。鉅亨網 9/23 的 MA 數值（2454、6196、0050）也一致。問題集中在四處：(1) 台股 enriched 用未還原價，分割與大額除權息會讓 MA、MACD 大幅失真並產生假訊號（0050 在 2025-06 分割、6669 在 2026-09 除權，目前仍受影響），也違反 CONTRIBUTING 的前復權契約；(2) 盤中增量路徑 live_agg 用 90 個日曆天代替 60 個交易日，2–5 月 MA60 低估 7–9%，新上市股的 MA20/30/60 不回傳 null、而是算出錯的數字；(3) 增量 MACD 的 _ema12/_ema26 只暖機 90 天，與 300 天暖機的 DEA 混用，實測 120 組「個股×交易日」中有 5 組金叉或死叉判斷被翻轉；(4) 盤中交叉訊號從第二輪輪詢起是拿上一輪快照比，而不是拿昨收比，當日交叉在第二輪就消失。另外，柱狀體沿用中國慣例 2×(DIF−DEA) 並標成「MACD」，與台灣慣例（MACD 指訊號線、OSC＝DIF−MACD、不乘 2）不同。其餘是次要項（P3）：啟動時的全量回退路徑讀錯資料表、MACD 沒有暖機門檻、無成交日在不同路徑的處理方式不一致、數值恰好相等時訊號結果取決於浮點誤差。所有腳本與輸出放在 AUDIT_SCRATCH/work/A02-ma-macd/。網路請求共 20 次，沒有連證交所或櫃買中心。

## 問題清單

### [P2 → P2] 盤中增量 MACD 混用兩種暖機長度，DIF 誤差最高約 5 點並翻轉金叉、死叉（確認成立）

- **主張**：live_agg 的 macd_dea（及 ema5–60）取自 300 個日曆天暖機的歷史快取，但 _ema12/_ema26 另外用只含 90 個日曆天的 df_hist，以第一根收盤當初值重算。今日的 DIF＝EMA12′−EMA26′ 帶著約 1% 的初值殘差，再與不同暖機長度的 DEA 相減，柱狀體與交叉判斷因此偏移。這與 code-review A6 記錄的 kline API 暖機差 0.017 是不同路徑、不同量級。
- **正確的定義或數值**：_ema12、_ema26、macd_dea 取自同一段（最好是完整）歷史的遞推狀態，增量結果等於全量結果。
- **app 實際行為**：_ema12/_ema26 只暖機約 60 個交易日，DEA 暖機約 200 個交易日，兩者混用後盤中 MACD 偏離收盤後的正本。
- **對使用者的影響**：盤中圖表活蠟燭的 DIF／DEA／柱狀體與收盤後的值不同（自選 2454 可差約 5 點 DIF、8 點柱），盤中 MACD 金叉、死叉判斷約 4% 的「個股×交易日」會錯。目前 0 條監控規則，主要影響圖表與盤中策略。
- **修法建議**：_ema12/_ema26 改由 hist_all（或完整歷史）遞推後取最後一列，與 macd_dea、ema* 同源；或直接把 _ema12/_ema26 保留在 compute_indicators 的輸出供 live_agg 使用。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:761-771（df_hist 只取 [latest−90 天, latest]；state_cols 含 macd_dea，取自 hist_all）
  - backend/app/repository.py:802-806 df_ema = … df_hist … ewm_mean(alpha=_ema_alpha(12)/…(26), adjust=False) → _ema12/_ema26
  - backend/app/indicators/pipeline.py:1503-1511 ema12/ema26 遞推 → dif；dea = α9·dif + (1−α9)·macd_dea
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential （"the current EMA value will change depending on how much past data you use… we calculate back at least 250 periods"）
- **本地計算證據**：
  - a02_incr.out [A]（10 檔 × 12 天，2026-09-08～09-23）：macd_dif 最大誤差 4.96、macd_hist 7.93（2454），相對收盤 2.6‰；近零值時相對誤差可到 94 倍。
  - MACD 交叉 5/120 列被翻轉：2379 在 9/17 全量為金叉、增量不是；2454 在 9/17 增量出現假死叉（全量 hist +2.88、增量 −1.35）；2379 在 9/18 假死叉；3034 在 9/18 漏掉金叉；3034 在 9/21 假死叉。
  - 因果驗證（a02_incr_cause.py）：把 _ema12/_ema26 改成與 macd_dea 相同的 300 天歷史後，DIF 最大差降到 2.5e-5，交叉零不一致。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：盤中增量路徑 `_build_live_agg` 的 `macd_dea`（以及 ema5～60）取自 300 個日曆天的歷史快取，`_ema12`、`_ema26` 卻只用 90 個日曆天（63～64 個交易日）的 `df_hist`，以第一根收盤當初值重算。EMA26 的初值殘差權重約 0.73～0.78%，EMA12 可忽略。今日 DIF 因此帶著 EMA26 的初值誤差，再和暖機較長的 DEA 相減，使盤中圖表活蠟燭與自選表的 DIF、DEA、柱狀體偏離收盤後的正本：2454 最大 DIF 差約 5 點、柱狀體差約 8 點。柱狀體正負號相對昨日的判斷（圖表上的金叉、死叉）在全市場約 2.8% 的「個股×交易日」翻轉，自選 5 檔在 9/08～9/23 有 5/60 次。把 `_ema12`、`_ema26` 改用與 `macd_dea` 同一段 300 天歷史後，誤差歸零。同一根因也讓盤中 RSI24 最多偏離約 3 點。影響限於盤中，收盤後重新整理即回到正本。
    - 理由：finding 成立，P2 恰當。原因是同一個 MACD 遞推用了兩段長度不同的歷史。

一、程式碼（現況行號與 finding 引用幾乎相同）
- `_build_live_agg` 的 `hist_all` 是 `_enriched_history_cache`，涵蓋 latest−300 個日曆天（repository.py:570、626、750）。`macd_dea`、`ema5`～`ema60` 取的是它最後一列的值（repository.py:766-779）。
- `_ema12`、`_ema26` 用 `df_hist` 另外重算，而 `df_hist` 只有 [latest−90 天, latest]（repository.py:746、761-763、802-808），並以第一根收盤當初值（`adjust=False`）。
- `compute_enriched_today` 用這兩組狀態遞推今天的值：DIF＝EMA12′−EMA26′，DEA＝α9·DIF＋(1−α9)·macd_dea（pipeline.py:1503-1512）。
- 收盤後的正本由 `compute_indicators` 在 300 天歷史上整段重算（repository.py:589）。兩條路徑的暖機長度不同，是確定的程式事實。

二、定義（兩個與 finding 不同的來源）
- Fidelity：MACD＝12 期 EMA－26 期 EMA，訊號線為 MACD 的 9 期 EMA，上穿、下穿分別是看多、看空。
- TA-Lib 的 Unstable Period 文件說明 EMA 有「記憶」，輸出取決於資料起點，而且這會影響建在 EMA 上的所有指標，包括 MACD。
- Wikipedia Exponential smoothing：α 越小，結果對初值越敏感。

所以 12、26、9 三條 EMA 的遞推狀態必須來自同一段、足夠長的歷史，增量結果才會等於全量結果；混用兩種暖機長度是實作缺陷，不是口徑選擇。理論殘差權重：(1−2/27)^63＝0.78%、^64＝0.73%，(1−2/13)^63＝2.7e-5。finding 寫「約 1%」略為高估，但量級正確；誤差幾乎全部來自 `_ema26`。

三、本機獨立重算（沒有使用稽核腳本）
- 做法：以 `types.SimpleNamespace` 當作 self，直接呼叫 `KlineRepository._build_live_agg` 與 `compute_enriched_today`，對照 300 天全量的 `compute_indicators`。
- 5 檔自選個股 × 12 天（9/08～9/23）：DIF 最大誤差 −4.9593、HIST 最大誤差 −7.9349（2454，9/08），與 finding 完全一致。
- 交叉翻轉 5/60 列，個股與日期也與 finding 列的完全相同：2379 在 9/17 漏掉金叉、9/18 假死叉；2454 在 9/17 假死叉（全量 hist＋2.879、增量 −1.351）；3034 在 9/18 漏掉金叉、9/21 假死叉。
- 把 `_ema12`、`_ema26` 改用同一段 300 天歷史後，DIF 最大差降到 9e-13，交叉零不一致，因果確認。
- 全市場：24,570 列中 677 列翻轉（2.76%）；只看歷史夠長（n90≥50）的，是 664/24,201（2.74%）。finding 的「約 4%」只代表它的 10 檔樣本，全市場約 2.8%，這 5 檔自選則是 8.3%。
- 價格核對：本機收盤價與官方 STOCK_DAY 參考檔一致（例如 2454 的 9/08＝4710、9/21＝5010）。

四、使用者情境下確實會發生
- 自選 5 檔個股走 `asset_type="stock"` 的增量路徑（quote_service.py:1003-1009 → 1716）。
- 圖表活蠟燭直接複製增量結果的 `macd_dif/dea/hist`（kline.py:576-584），前端直接畫這些欄位（StockDailyKChart.tsx:77-79）。
- 自選表的 API 也讀同一份 `get_enriched_latest`（watchlist.py:262-271、309）。
- 收盤後重新整理，數值會回到正本，所以影響限於盤中。監控規則目前 0 條。

需要修正的細節：
- 初值殘差是 0.73～0.78%，不是約 1%。
- 盤中快照的 `signal_macd_golden/dead`，在第一次輪詢之後是和「前一次輪詢」比，而不是和昨日比：merge 後 `_enriched_cache_date` 會變成今天（repository.py:2302-2304），而 `_flush_live_enriched` 從它取 prev_enriched（quote_service.py:1702-1706）。所以「翻轉率」精確的意思是「今日柱狀體正負號相對昨日的判斷錯誤」，也就是圖表上看到的交叉；它不等於每次輪詢的訊號欄位都錯。
- 同一根因的新面向：RSI 的遞推狀態也只取 90 天 `df_hist`（repository.py:816-833），全量則用 300 天（pipeline.py:541-542）。實測 5 檔 × 12 天，RSI24 最大差 2.95、中位數 0.63，RSI14 最大差 0.63；finding 沒有提到。

嚴重度：MACD 公式本身正確，收盤後會自動回到正本，不構成 P1 的錯誤口徑；但盤中顯示值與交叉判斷明確錯誤，屬於 P2。docs/code-review-2026-09-22.md 只記錄 A6 的 150 天對 300 天差 0.017（kline API 路徑），沒有這條 live_agg 路徑，不是重複回報。
    - 證據：
      - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)；761-763 df_hist 只取 [latest−90天, latest]
      - backend/app/repository.py:766-779 state_cols 含 ema5–ema60、macd_dea，取自 hist_all（_enriched_history_cache）
      - backend/app/repository.py:570,589,626 _enriched_history_cache＝compute_indicators(latest−300 天)
      - backend/app/repository.py:802-808 _ema12/_ema26 以 df_hist 做 ewm_mean(adjust=False)
      - backend/app/indicators/pipeline.py:1503-1512 ema12/ema26 遞推 → dif；dea＝α9·dif＋(1−α9)·macd_dea
      - backend/app/services/quote_service.py:1003-1009,1716 自選個股走 stock 增量路徑；backend/app/api/kline.py:576-584 活蠟燭複製 macd_dif/dea/hist；frontend/src/components/StockDailyKChart.tsx:77-79 前端直接使用
      - https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd ："subtracting the value of a 26 period Exponential Moving Average (EMA) from a 12 period EMA"；"A signal line is created with a 9 period EMA of the MACD line"
      - https://ta-lib.org/api/unstable-period/ ："Some indicators have 'memory'. Each output depends on the previous one, seeded from the start of the data. An Exponential Moving Average is the classic example."；EMA 的設定會影響 "every indicator built on one, such as MACD"
      - https://en.wikipedia.org/wiki/Exponential_smoothing ："the smaller the value of α, the more sensitive the forecast will be on the selection of this initial smoother value s0"
      - 本機：uv run python scratchpad\audit\work\verify_macd_warmup\verify.py watch → max |e_dif| 4.9593、max |e_hist| 7.9349（2454 2026-09-08）；cross_flip 5/60（2379 9/17、9/18；2454 9/17；3034 9/18、9/21）；改用 300 天的 _ema12/_ema26 後 max |e_dif| 9.09e-13
      - 本機：verify.py all（全市場）→ cross_flip 677/24570（2.76%）；n90≥50 時 664/24201；max |e_dif| 13.35
      - 本機：理論殘差 (1−2/27)^63＝0.00784、^64＝0.00726；(1−2/13)^63＝2.69e-5；df_hist 的交易日數 n90＝63～64
      - 本機：verify_rsi.py watch → RSI24 增量與全量最大差 2.95、中位數 0.63；RSI14 最大差 0.63（同一根因：backend/app/repository.py:816-833 RSI 狀態取自 90 天 df_hist，對照 backend/app/indicators/pipeline.py:541-542 全量 300 天）
      - 官方參考資料 csv/twse_stock_day_202608_202609.csv：2454 收盤 9/08＝4710、9/17＝4500、9/21＝5010；3034 9/18＝558；2379 9/18＝727，與本機資料一致
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：live_agg 的 macd_dea 與 ema5–60 取自 300 個日曆天（約 199 個交易日）計算的歷史快取，但 _ema12/_ema26 另外用只含 90 個日曆天（約 63 個交易日）的 df_hist，以第一根收盤當初值重算（repository.py:746、761-763、802-808）。誤差幾乎全部來自 EMA26：初值殘差約為 (25/27)^63≈0.78% 乘上「第一根收盤與真實 EMA26 的差」，EMA12 的殘差（約 0.003%）可以忽略。

2026-09-08～09-23 期間 10 檔 × 12 天的實測結果：增量 DIF 最大誤差 4.96、柱狀體最大誤差 7.93（2454，相對收盤約 1.05‰）；相對收盤的最大值是 2379 的 2.6‰；近零時柱狀體相對誤差可達 94 倍（3545）。MACD 金叉、死叉與柱狀體正負號有 5/120 列（約 4%）與完整歷史正本不一致。只要讓 _ema12/_ema26 與 macd_dea 同樣用 300 天的遞推狀態，DIF 最大差就降到 2.5e-5，交叉不一致變成 0。

這些錯誤值會出現在盤中活蠟燭（kline.py:576-584）與盤中增量訊號；收盤後 enriched_refresh 會用完整歷史覆寫，所以不會落檔。附註：首輪輪詢之後，prev_enriched 會變成今天前一輪的快照，因此盤中交叉旗標實際比較的是前一輪輪詢；4% 的數字是「與昨日比較」語意下的量測值。
    - 理由：我寫了自己的腳本重現，沒有沿用稽核的腳本。做法：把 SimpleNamespace 當成假的 self，直接呼叫正式程式碼 KlineRepository._build_live_agg，再呼叫 compute_enriched_today，模擬 2026-09-08～09-23 共 12 個交易日、10 檔（自選 9 檔加 2330）。全量正本則從 data/taiwan/daily 的完整歷史（2024-01-02 起）自己用 compute_indicators 重算。重算的正本和磁碟上 kline_daily_enriched 的 macd_dif、macd_dea 在 6604 列中差值都是 0，所以「全量正本」這個比較基準本身可信。

finding 的每個數字都完全重現：
- 暖機長度：_ema12/_ema26 實際只用 63～64 個交易日，macd_dea 用 199 個交易日。
- DIF 最大誤差 4.959287，柱狀體最大誤差 7.934894，都出現在 2454 的 2026-09-08。
- 近零值時的相對誤差最大 94.06 倍，出現在 3545 的 9/17，柱狀體正本 −0.000254、增量 −0.024139。
- MACD 交叉不一致 5/120，清單和 finding 逐筆相同：2379 在 9/17 漏掉金叉；2454 在 9/17 出現假死叉（柱狀體正本 +2.879185、增量 −1.351072）；2379 在 9/18 假死叉；3034 在 9/18 漏掉金叉；3034 在 9/21 假死叉。
- 因果驗證：只把 _ema12/_ema26 換成與 macd_dea 同樣 300 天歷史的遞推值，DIF 最大差就降到 2.55e-5，交叉不一致變成 0；全部狀態改用完整歷史時，DIF 最大差是 9.1e-13。所以根因確實是兩種暖機長度混用。
- 盤中狀態確實會用到這個值：kline.py 會把增量算出的 macd_dif/dea/hist 注入活蠟燭，docs 第 116 行也寫明活蠟燭走同一套增量計算。這和 A6 記錄的 kline API 暖機差 0.017 是不同路徑、不同量級，不算重複回報。
- 線上佐證：StockCharts 原文確實寫著 "the current EMA value will change depending on how much past data you use" 和 "we calculate back at least 250 periods"。

有兩處敘述需要修正：
1. 「相對收盤 2.6‰」不是 2454 的數字：2454 自己的最大值是 1.05‰（4.96/4710）；2.6‰ 是所有檔中的最大值，屬於 2379。
2. 「約 1% 的初值殘差」實際上幾乎只來自 EMA26：(25/27)^63≈0.78%，而 EMA12 是 (11/13)^63≈0.0027%，可以忽略。以 2454 的 9/08 為例，_ema26 用 90 天是 4059.82、用 300 天是 4054.46，差 5.36；_ema12 只差 0.007。

另外補充一個背景：第一輪輪詢之後，merge_live_enriched_asset 會把 _enriched_cache 換成今天的快照，此後 prev_enriched 取的是前一輪輪詢，不是昨日。因此「4% 的個股×交易日交叉判斷錯誤」嚴格說是「首輪／收盤時與昨日比較」這種語意下的數字。不過 DIF 與 DEA 的相對位置（柱狀體正負號）同樣在 5/120 列錯誤，結論不變。收盤後 enriched_refresh 會用完整歷史重寫分區，錯誤不會落檔。所以維持 P2：功能錯誤，只影響盤中顯示和盤中策略，不是資料破壞，而且目前 0 條監控規則。
    - 證據：
      - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)；:761-763 df_hist 只取 [latest−90天, latest]；:766-781 macd_dea／ema* 取自 hist_all（300 天計算的快取）
      - backend/app/repository.py:571 start_full = latest - timedelta(days=300)；:589 df_full = compute_indicators(df_hist) → _enriched_history_cache
      - backend/app/repository.py:802-808 _ema12/_ema26 以 df_hist（90 天）做 ewm_mean(adjust=False)，初值取第一根收盤
      - backend/app/indicators/pipeline.py:1504-1512 ema12/ema26 遞推 → dif；dea = α9·dif + (1−α9)·macd_dea
      - backend/app/taiwan/enriched_refresh.py:65-95 正本一律從完整歷史計算（docstring 明寫截斷暖機會翻轉交叉）
      - backend/app/api/kline.py:576-584 活蠟燭注入增量結果的 macd_dif/macd_dea/macd_hist
      - 腳本 AUDIT_SCRATCH/work/verify-macdmix/repro.py（直接呼叫正式的 KlineRepository._build_live_agg 與 compute_enriched_today）；輸出 repro.out、repro_result.csv
      - 指令：cd backend && PYTHONIOENCODING=utf-8 uv run python .../verify-macdmix/repro.py → 『canon(full history) vs on-disk enriched: max|dif diff|=0.000e+00 max|dea diff|=0.000e+00 over 6604 rows』
      - 本地輸出：n90（_ema12/_ema26 暖機）=63～64 個交易日；n300（macd_dea 暖機）=199 個交易日
      - 數值對照（finding → 我的重現）：DIF 最大誤差 4.96 → 4.959287（2454 的 2026-09-08：正本 184.27484、增量 179.315554）；柱狀體 7.93 → 7.934894；近零相對誤差 94 倍 → 94.064（3545 的 9/17：正本 −0.000254、增量 −0.024139）；交叉翻轉 5/120 → 5/120，名單逐筆相同
      - 交叉翻轉明細：2379 9/17 正本金叉、增量無（柱狀體 +0.7398 對 −1.5747）；2454 9/17 增量假死叉（+2.879185 對 −1.351072）；2379 9/18 假死叉（+3.0449 對 −0.0364）；3034 9/18 漏金叉（+0.7123 對 −0.2462）；3034 9/21 假死叉（+0.1532 對 −0.7346）
      - 因果：_ema12/_ema26 改用 300 天 → DIF 最大差 2.549986e-05、交叉不一致 0；全部狀態改用完整歷史 → DIF 最大差 9.09e-13；300 天批次快取本身對正本的交叉不一致也是 0
      - 需修正處 1：相對收盤最大值 0.002649 屬於 2379（close 761）；2454 自身的最大值是 0.001053
      - 需修正處 2：2454 9/08 的 _ema26：90 天 4059.822683、300 天 4054.460041、完整歷史 4054.460056；_ema12：90 天 4210.444887、完整歷史 4210.437667。殘差係數 (25/27)^63=0.00784、(11/13)^63=2.69e-5
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential 原文："the current EMA value will change depending on how much past data you use"；"we calculate back at least 250 periods (typically much further)"
      - 工作樹前後 git status --porcelain 相同，仍只有原本 3 個檔案的修改（daily_update.py、pnpm-lock.yaml、vite.config.d.ts），我沒有寫入 repo 或 data

### [P2 → P2] 盤中交叉訊號從第二輪輪詢起拿上一輪快照比，而不是拿昨收比（確認成立）

- **主張**：_flush_live_enriched 每輪都用 get_enriched_latest() 當 prev_enriched。第一次 merge 後，_enriched_cache 被換成今日的池內快照，_enriched_cache_date 也變成今天。之後每輪的 _prev_ma5、_prev_dif、_prev_close_enriched 等全是上一輪（數秒前）的盤中值，交叉訊號因此變成「輪詢之間」的交叉，不再是「今日相對昨收」的交叉。code-review A5 只提到 prev_enriched 是池內快照、池外標的訊號可能為空，沒有提到池內標的會跟上一輪比。
- **正確的定義或數值**：盤中所有輪詢都以最後一個完整交易日（昨收）的指標判斷交叉。
- **app 實際行為**：只有當日第一輪（或重啟後第一輪）正確；之後當日真正的交叉會消失，盤中價格在均線附近來回時則每次穿越都會觸發。
- **對使用者的影響**：目前 0 條監控規則，所以沒有誤發告警。但一旦建立 MA／MACD 交叉或突破類規則，或盤中策略引用這些訊號，會漏掉當日交叉，並在盤中震盪時重複觸發。
- **修法建議**：prev_enriched 改取「日期早於今天」的最後一日 enriched（例如 live_agg 基準日的歷史快取列），不要用當日快取；或在 live_agg 裡保存昨日的 ma5/10/20/60、macd_dif/dea、close。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/services/quote_service.py:1702-1703 prev_enriched, prev_date = self._repo.get_enriched_latest()
  - backend/app/repository.py:2255 existing_cache = self._enriched_cache if self._enriched_cache_date == dt else pl.DataFrame()
  - backend/app/repository.py:2303-2304 self._enriched_cache = merged_cache; self._enriched_cache_date = dt
  - backend/app/indicators/pipeline.py:1625-1667（以 prev_enriched 的 ma／macd／close 判斷交叉）
  - backend/app/indicators/pipeline.py:1428 docstring 寫「昨天的完整 enriched (用于信号交叉判断)」
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator （交叉以相鄰兩個週期，即日K的昨日與今日判斷）
- **本地計算證據**：
  - a02_prevpoll.py（目標 2026-09-08，基準 09-07）：第 1 輪（prev＝昨日）觸發 8 個訊號，與全量一致：3034 的 ma_dead_5_20 與 ma20_breakdown，00885、2379、3545、6196 的 ma5_breakdown，00885、2379 的 ma10_breakdown。第 2 輪（prev＝第 1 輪輸出、同一根 K）8 個全部變成 false。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立，只需修正影響描述的細節。

**機制**：盤中股票增量路徑（台股部署中 9 檔自選含 ETF 都走這條）每輪都以 get_enriched_latest() 當 prev_enriched，而且沒有檢查日期早於今天。第一輪 merge 後，_enriched_cache 與日期就換成今日的池內快照（盤中只在記憶體）。因此從當日第二輪起，MA／MACD 交叉與 MA5/10/20 突破訊號比的是上一輪（數秒前）的盤中值，不是昨收。這和同一個 repo 的日K／回測定義不一致（compute_signals 用 shift(1)），也不符合業界對即時K棒「以前一根已確認K棒為前值」的做法（TradingView 執行模型）。只有當日 09:00 後第一輪，以及盤中重啟後第一輪是正確的。

**結果**（均線、MACD 數值本身正確，錯的只有布林值訊號）：
- 真正的交叉在第一輪之後會從訊號欄位消失。影響 level 模式規則，以及直接讀今日快取訊號的消費者。
- 盤中會出現與昨日狀態矛盾的假交叉，例如昨日已在下方卻報死叉。
- 以及只相對於上一輪快照、日線層級並不存在的交叉。
- 在預設的 edge 觸發加 3600 秒冷卻下，重複告警每小時最多一次；正確口徑在 edge 模式下同方向的震盪也會重新觸發。這個 bug 特有的是反向假交叉，以及對快照的交叉。

**使用者情境**：目前 0 條規則，台股自選行的 signal_* 一律 None，所以目前沒有可見影響。一旦建立交叉或突破類規則，或盤中選股讀今日快取，就會出錯。

**嚴重度**：P2 恰當。

**修法方向**：prev_enriched 改取日期早於今天的最後一日列（例如 live_agg 基準日的歷史快取），或在 live_agg 保存昨日的 ma／macd／close。
    - 理由：我讀了現行程式碼，也用真實的程式路徑重現，finding 的核心主張成立。

1. 程式路徑：_flush_live_enriched（quote_service.py:1702-1706）每一輪都用 get_enriched_latest() 取 prev_enriched，而且沒有檢查 prev_date 是否早於今天（1708-1713 只檢查非空、非 None）。get_enriched_latest（repository.py:1125-1137）直接回傳 _enriched_cache。merge_live_enriched_asset 則在 2303-2304 把 _enriched_cache 換成今日的合併快照，_enriched_cache_date 設為今天。盤中今日尚無官方資料，_live_write_allowed（repository.py:1396-1410）不會寫盤，但記憶體快取照樣更新。所以從第二輪起，compute_enriched_today（pipeline.py:1625-1667）的 _prev_ma5／_prev_ma20／_prev_dif／_prev_dea／_prev_close_enriched 都是上一輪的盤中值。另一方面，ma5 等指標數值取自 live_agg 的昨日遞推狀態（pipeline.py:1515-1521），數值本身正確，錯的只有「交叉訊號」的比較基準。

2. 定義：同一個 repo 的日K和回測訊號 compute_signals（pipeline.py:622-641）用 shift(1).over("symbol")，也就是前一根日K。TradingView 官方 Pine 文件（與 finding 引用的 StockCharts 是不同來源）規定：未收盤的即時K棒每次重算前都先 rollback 到上一根K棒收盤時的狀態，close[1] 指的是前一根已確認的K棒，不是前一個 tick。所以正確的盤中口徑是「今日活蠟燭對昨收」，finding 的 expected 正確。

3. 我自己寫的重現（沒有沿用 a02_prevpoll.py）：用 QuoteService._flush_live_enriched 加真實的 KlineRepository（store 放在 scratchpad，_official_dates 設為空集合，模擬盤中），連跑 4 輪。第 1 輪 prev 是 09-23，三種金叉和突破都是 True，與參考值一致。第 2 輪 prev 變成 09-24，也就是上一輪的值：ma5 10.8 對 ma20 10.675。golden_5_20、ma20_breakout、macd_golden 全部變成 False，但以昨收比應為 True。第 3 輪（close 11）出現 ma_dead_5_20=True，以昨收比應為 False：昨日 ma5 9.9 本來就低於 ma20 10.4，這是假死叉。第 4 輪又觸發一次 golden。沒有寫出任何分區，repo 的 git status 前後一致。

4. 使用者情境：instruments_etf 是空的，0050／00878／00885／00893 都在 kline_daily_enriched 的 2026-09-23 分區裡，所以 9 檔自選全部走股票的增量路徑，全部受影響。開盤前試撮的狀態是 OFFICIAL_SNAPSHOT（mis_provider.py:209-213），會被 _records_datable_today（quote_service.py:1068-1070）濾掉，所以當日第一次 flush 發生在 09:00 之後的第一輪。盤中重啟時磁碟上最新的仍是昨日分區，重啟後第一輪也正確。finding 說「只有第一輪（或重啟後第一輪）正確」，屬實。

5. 需要修正的細節：
(a) 「每次穿越都會觸發」會被監控引擎限縮。預設是 edge 模式，冷卻 3600 秒（monitor.py:1269-1306、897-908），同一組規則、標的、事件每小時最多觸發一次。而且就算用正確口徑，edge 模式在震盪時同方向的交叉也會重新觸發。這個 bug 特有的錯誤是：與昨日狀態矛盾的反向假交叉，例如昨日已經在下方卻報死叉，以及只相對於盤中快照、日線層級並不存在的交叉。
(b) 「漏掉當日交叉」：edge 規則在第一輪就會觸發，不會漏。真正受影響的是 level 模式的持續重播，以及任何讀取今日快取訊號欄位的地方（例如 screener._load_enriched_for_date 在 cache_date==target_date 時直接用快取）。
(c) 目前沒有可見影響：監控規則 0 條，台股自選行的 signal_* 一律是 None（watchlist_enrichment.py:20-23），K線注入只取指標數值（code-review A5 第 121-122 行）。
(d) 這是新面向：code-review A5 只寫到「prev_enriched 是池內快照、池外標的訊號可能為空」，沒有提到池內標的會跟上一輪比。

6. 嚴重度：P2（明確的功能錯誤）合理。它和回測、日K的定義不一致，也可以主張是口徑錯誤，但目前實際曝險為零，而且只影響布林值訊號、不影響指標數值，所以不升到 P1。
    - 證據：
      - backend/app/services/quote_service.py:1702-1713：prev_enriched, prev_date = self._repo.get_enriched_latest()；use_incremental 只檢查非空，也只檢查 prev_date is not None，沒有 prev_date < today 的條件
      - backend/app/repository.py:1125-1137：get_enriched_latest 直接回傳 self._enriched_cache 與 self._enriched_cache_date
      - backend/app/repository.py:2255、2266-2271、2302-2304：existing_cache 只在同一天時合併；合併後 self._enriched_cache = merged_cache，self._enriched_cache_date = dt（今天）；盤中不寫盤也照樣更新（2252、1396-1410）
      - backend/app/indicators/pipeline.py:1625-1667：交叉訊號以 prev_enriched 的 ma5/ma10/ma20/ma60/macd_dif/macd_dea/close 當作前值；1428 行 docstring 寫「昨天的完整 enriched」
      - backend/app/indicators/pipeline.py:622-641：日K／回測版 compute_signals 以 shift(1).over("symbol")（前一根日K）判斷交叉，與盤中路徑口徑不一致
      - backend/app/repository.py:706-712：_restore_missing_latest_rows 自己就用 history.filter(date < latest) 取前值，表示原設計的前值是前一交易日
      - 線上（與 finding 不同的來源）https://www.tradingview.com/pine-script-docs/language/execution-model/ ："Before each new script execution on an open bar, the runtime system executes a rollback process, which reverts all applicable variables, expressions, and objects to their last committed states as of the previous bar's close."；並指出 offset 1 指的是 bar_index - 1 那根K棒（盤中不是前一個 tick）
      - 線上 https://pinewizards.com/technical-analysis-functions/using-the-ta-crossover-function-in-pine-script/ （WebSearch 摘要）：crossover 的條件是當前K棒 source1 > source2，且前一根K棒 source1 <= source2
      - 本地重現：cd backend && uv run python AUDIT_SCRATCH/work/v_prevpoll_def/repro.py；輸出節錄：poll 1 prev_used(date=2026-09-23) golden_5_20 live=True vs_yesterday=True；poll 2 prev_used(date=2026-09-24, ma5=10.8000, ma20=10.6750) golden_5_20 live=False vs_yesterday=True、ma20_breakout live=False vs_yesterday=True、macd_golden live=False vs_yesterday=True；poll 3 close=11.0 ma_dead_5_20 live=True vs_yesterday=False（假死叉）；poll 4 golden_5_20 再次 True；partitions written: []
      - 使用者情境：data\instruments_etf 與 data\kline_etf_enriched 都是空目錄；data\kline_daily_enriched\date=2026-09-23 含 0050.TWSE(112.45)、00878.TWSE(35.09)、00885.TWSE(18.27)、00893.TWSE(46.19)、2454.TWSE(5185.0)，所以 9 檔自選全部走股票增量路徑
      - backend/app/taiwan/realtime/mis_provider.py:209-213 與 backend/app/services/quote_service.py:1068-1070：開盤前試撮的狀態是 OFFICIAL_SNAPSHOT，會被濾掉，所以當日第一次 flush 在 09:00 之後
      - backend/app/strategy/monitor.py:1269-1306、318-337、897-908：預設 edge 觸發，條件不命中即重新 armed，冷卻 3600 秒，所以震盪時的重複觸發每小時最多一次
      - backend/app/taiwan/watchlist_enrichment.py:20-23：台股自選行的 signal_* 一律 None，目前前端看不到受影響的訊號
      - docs/code-review-2026-09-22.md:120-122：A5 只提到 prev_enriched 是池內快照、池外標的訊號可能為空，沒有提到池內標的會跟上一輪比
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_flush_live_enriched 每輪都把 get_enriched_latest() 當成 prev_enriched（quote_service.py:1702-1703）。當日第一次合併後，merge_live_enriched_asset 把 _enriched_cache 換成今日池內快照，日期設為今天（repository.py:2255、2303-2304）。之後每輪的 _prev_ma5／_prev_dif／_prev_close_enriched 等都是上一輪的盤中值，MA／MACD 交叉與均線突破／跌破訊號因此變成「輪詢之間」的比較，不再以昨收為基準。

實測 2026-09-08（官方 OHLCV）：第 1 輪觸發 8 個訊號，與全量計算、磁碟分區一致；第 2 輪起同一根 K 觸發 0 個。後果有兩種：
- 價格停在均線同側時，當日真正的交叉會被漏掉。
- 價格在均線兩側來回時，會多出方向相反的假訊號，例如 00885 在昨收站上 MA5 的情況下，盤中回到 MA5 之上仍觸發 ma5_breakout。以昨收為準時，跌破訊號本來就會隨價格切換，這部分不算誤觸。

影響方面，目前 0 條監控規則，所以沒有誤發告警；但即時行情開啟時，自選頁（watchlist.py:309 → Watchlist.tsx getSignals）顯示的「MA死叉／跌破MA20／MACD金叉」等標籤，盤中第 2 輪起就會消失或失真，要等盤後刷新才恢復。一旦建立交叉類監控規則，或盤中策略引用這些訊號，也會漏報或誤報。磁碟不存訊號欄，盤後全量重算可以修正，沒有資料破壞。
    - 理由：我寫了自己的腳本重現這條 finding。腳本實際呼叫專案的 QuoteService._flush_live_enriched 與 KlineRepository.merge_live_enriched_asset／get_enriched_latest／_build_live_agg，repo 用 object.__new__ 建立：store 指向 scratch，_official_dates 設為空集合，所以不會寫盤；事後以 find -newer 確認 data\ 沒有任何新檔。今日 K 採用官方參考資料 twse_stock_day 在 2026-09-08 的 OHLCV，前收取 09-07 收盤價；9 檔的 raw_close 與本地 parquet 核對零差異。

結果：
- 第 1 輪（prev_enriched＝09-07，共 9 列）觸發 8 個訊號：00885 的 ma5／ma10_breakdown、2379 的 ma5／ma10_breakdown、3034 的 ma_dead_5_20 與 ma20_breakdown、3545 的 ma5_breakdown、6196 的 ma5_breakdown。這與全量 compute_indicators＋compute_signals 算到 09-08 的結果一致，也與磁碟上 date=2026-09-08 分區存的訊號一致，和 finding 列的 8 個也完全相同。
- 第 1 輪合併後，快取日期變成 09-08。第 2、3 輪（同一根 K）的 prev_enriched 日期是 09-08，拿來比的收盤價是 00885＝18.47、3034＝534.0（上一輪自己的值），觸發訊號數為 0。這與 finding 的「8 個全部變成 false」相同。

程式碼鏈確認：
- quote_service.py:1702-1703 每輪都取 get_enriched_latest()。
- repository.py:2255 在日期不同時丟掉舊快取；2303-2304 把快取換成今日池內快照，日期設為今天。
- 當天沒有任何路徑會把 _enriched_cache 重設回昨日（clear_cache／refresh_cache 只在管線、API 手動刷新與啟動時呼叫）。
- live_agg 仍停在昨日基準（repository.py:1255-1268 只在跨日時重建），所以只有交叉判斷的基準錯了，指標數值沒有錯。
- 全量路徑 pipeline.py:623-653 用 shift(1).over("symbol")，也就是上一個交易日，這是專案自己對交叉的定義。pipeline.py:1428 的 docstring 也寫明「昨天的完整 enriched」。
- 線上參考 StockCharts ChartSchool 的定義是 "A bullish crossover occurs when the shorter moving average crosses above the longer moving average"，並以日線逐日判斷。

需要修正兩處：

(1)「盤中價格來回時每次穿越都會觸發」要說精確。我用 00885 模擬價格路徑 18.47→18.80→18.47→18.80→18.47（昨收 18.73，昨日 MA5 為 18.636）。實際程式在第 2、4 輪觸發了 ma5_breakout，但以昨收為準的正確值是 false，這是多出來的假訊號。ma5_breakdown 在第 1、3、5 輪為 true，這在正確口徑下本來就是 true。所以錯誤的是：價格一旦停在同側就漏報；在均線兩側來回時，多出方向相反的假訊號。

(2)影響不只「將來建立規則才會發生」。自選頁 API（backend/app/api/watchlist.py:309 用 get_enriched_latest；_WATCHLIST_COLS 在 253-272 行，含 signal_ma_dead_5_20、signal_ma20_breakdown、signal_macd_golden 等）與前端 Watchlist.tsx:496／1808 的 getSignals 會顯示「MA死叉」「跌破MA20」「MACD金叉」等標籤。在目前的使用情境（自選 9 檔、即時行情開啟）下，盤中從第 2 輪起這些標籤就會消失，或變成輪詢之間的交叉，要等盤後刷新快取才恢復。磁碟只寫 storage 欄，盤後全量重算會修正，所以沒有資料破壞。維持 P2。

這條 finding 的核心沒有被 code-review A5 涵蓋：A5 只談池外標的的訊號可能為空。
    - 證據：
      - backend/app/services/quote_service.py:1702-1703 每輪都用 prev_enriched, prev_date = self._repo.get_enriched_latest()
      - backend/app/repository.py:2255 existing_cache = self._enriched_cache if self._enriched_cache_date == dt else pl.DataFrame()；2303-2304 self._enriched_cache = merged_cache; self._enriched_cache_date = dt
      - backend/app/indicators/pipeline.py:1625-1667 增量路徑以 prev_enriched 的 ma/macd/close 判斷交叉；pipeline.py:623-653 全量路徑用 shift(1).over("symbol")，也就是上一交易日；pipeline.py:1428 docstring 寫「昨天的完整 enriched (用于信号交叉判断)」
      - 腳本 AUDIT_SCRATCH/work/verify-prevpoll/repro_prevpoll.py，執行方式：cd backend && uv run python <腳本>。實際呼叫 QuoteService._flush_live_enriched 與 merge_live_enriched_asset，今日 K 為官方 twse_stock_day 2026-09-08 資料
      - 輸出 round 1：prev_enriched date=2026-09-07 rows=9 -> cache date=2026-09-08；fired 8 個：00885 ma5_breakdown、ma10_breakdown，2379 ma5_breakdown、ma10_breakdown，3034 ma_dead_5_20、ma20_breakdown，3545 ma5_breakdown，6196 ma5_breakdown（與 finding 的 8 個完全相同）
      - 輸出 round 2、3：prev_enriched date=2026-09-08，拿來比的收盤價 00885=18.47、3034=534.0（上一輪自己的值），fired: []（finding 為 8 個全部變成 false，一致）
      - 對照：全量 compute_indicators＋compute_signals 算到 09-08，以及磁碟 date=2026-09-08 分區存的訊號，兩者都是同樣 8 個；官方收盤價與本地 raw_close 差異為 []
      - 震盪模擬 00885（昨收 18.73，昨日 MA5 18.636），路徑 18.47→18.80→18.47→18.80→18.47：實際 (breakout, breakdown)＝(F,T),(T,F),(F,T),(T,F),(F,T)；以昨收為準的正確值＝(F,T),(F,F),(F,T),(F,F),(F,T)。第 2、4 輪多出假的 ma5_breakout
      - backend/app/api/watchlist.py:309 用 repo.get_enriched_latest()；watchlist.py:253-272 _WATCHLIST_COLS 含 signal_ma_dead_5_20、signal_ma20_breakdown、signal_macd_golden 等；frontend\src\pages\Watchlist.tsx:496／1808 用 getSignals(r) 顯示標籤；frontend\src\lib\stock-table.ts:20-35 SIGNAL_FIELDS
      - docs/code-review-2026-09-22.md:120-122 A5 只提到池外標的的訊號可能為空，沒有提到池內標的會跟上一輪比
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential："A bullish crossover occurs when the shorter moving average crosses above the longer moving average." "A bearish signal is generated when prices move below the moving average."（以日線逐日判斷）
      - 寫入檢查：find data/ -newer marker 無結果；腳本只在 scratch 的 fake_data 建立空目錄

### [P3 → P3] 盤中全量回退路徑讀 kline_daily（台股部署幾乎是空的），啟動預熱期間活蠟燭的 MA／MACD 錯誤（確認成立）

- **主張**：live_agg 為空時（例如 _enriched_warming 預熱期間），_flush_live_enriched 改走全量回退，但歷史來源是 kline_daily，而台股部署從不寫入這張表（enriched_refresh 的 docstring 自己也這樣寫）。算出的 MA20／MA60 為 null，MA5 跨越不連續的日期，MACD 只從約 5 根 K 暖機。
- **正確的定義或數值**：台股部署的回退路徑讀 TaiwanDailyStore，或在沒有完整歷史時不輸出指標。
- **app 實際行為**：回退時輸出錯誤、不完整的指標，而且會成為下一輪的 prev_enriched。
- **對使用者的影響**：啟動後預熱的數十秒內（N1 也提到當日第一輪會走回退），活蠟燭的 MA、MACD 顯示錯誤或空白，下一輪即恢復。影響短暫。
- **修法建議**：台股 provider 的回退歷史改讀 TaiwanDailyStore.read_range；或回退時只輸出 OHLCV、不輸出指標。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/services/quote_service.py:1706-1711（use_incremental 需要 live_agg 與 prev_enriched 都非空）
  - backend/app/services/quote_service.py:1729-1741（cutoff＝today−90 天；table＝kline_daily）
  - backend/app/taiwan/enriched_refresh.py:3-5 「The indicator pipeline only sources kline_daily, which the Taiwan deployment never populates」
  - backend/app/repository.py:1247-1249（預熱中 get_live_agg 回空表）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - data/kline_daily 只有 4 個分區、33 列（2026-09-16、17、18、22，缺 09-21）。
  - a02_fallback.py 以這些資料加 9/23 的 K 重現回退：2454 的 ma5／ma20／ma60 全為 null（實際 4917／4531.25／4067.17），DIF 80.41（實際 262.75）；0050 的 ma5 109.0（實際 110.71），ma20／ma60 為 null。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：live_agg 為空時（實際只發生在啟動預熱期間，repository.py:1249-1251），_flush_live_enriched 會改走全量回退，但歷史來源是 kline_daily（quote_service.py:1729-1743）。純台股部署的這張表只有 4 個分區、33 列，而且全部是 B3 修正（f43c7bd，2026-09-22 19:36）之前寫入的汙染資料：每一列都是前一交易日的官方 K，被標成下一個日期（分區 9/16、9/17、9/18、9/22 分別是官方 9/15、9/16、9/17、9/21 的 K）。B3 的修法只擋新寫入，沒有清理這些舊分區。因此回退算出的活蠟燭指標是錯的：MA20 與 MA60 全部為 null；MA5 在歷史不足 4 列的股票（2454、2379、3034）為 null，其餘股票用的是錯位又不連續的 5 根 K（0050：109.0 vs 110.71）；MACD 只用 4 到 5 根 K 暖機（2454 DIF 80.41 vs 262.75）。錯誤列會併進 _enriched_cache，成為下一輪的 prev_enriched，可能讓下一輪產生一次假的交叉訊號，例如以 9/23 收盤模擬時，00885 會出現假的 MACD 死叉。MA 與 MACD 不會寫進磁碟。依 log，預熱耗時 1～14 秒，平均約 2～4 秒，通常只有啟動後第一輪輪詢（最多 2 到 3 輪）受影響，下一輪即恢復。監控規則為 0 條，所以不會觸發告警。
    - 理由：我照回退路徑（quote_service.py:1729-1768）的讀法自己寫腳本重現：歷史取自 kline_daily，今天設為 2026-09-23，排除今日的列後接上 9/23 的 K，再以 factors 為空、instruments 為空的條件呼叫 compute_enriched。finding 列的數值全部重現，一個小數位都沒差：2454 的 ma5／ma20／ma60 全為 null；正確值是 4917／4531.25／4067.17，TaiwanDailyStore 全歷史重算、磁碟上 9/23 的 enriched 分區、官方 STOCK_DAY 算出的 MA5／MA20 三方一致。2454 回退算出的 DIF 是 80.4148，正確值 262.749。0050 回退的 ma5 是 109.0，正確值 110.71；ma20、ma60 為 null。MACD 只用 4 到 5 根 K 暖機，這點也對（2454 有 4 根，0050 有 5 根）。TaiwanDailyStore 的收盤價與官方參考資料逐筆比對 342 列，0 筆不符，可以當正確值的基準。

觸發條件也成立。get_live_agg 在預熱期間回傳空表（repository.py:1249-1251），這時 use_incremental 為 False（quote_service.py:1708-1713）。backend.log、backend.log.1、backend.log.3 共有 166 筆「enriched 全量计算 (live_agg=空…)」，全部發生在 app ready 之後 15 秒內。9/18 有一次真實重啟：09:17:08 走回退，09:17:17 就回到增量，和「下一輪即恢復」相符。

finding 有兩處敘述需要修正，但不影響結論：
(1) 「缺 09-21」的說法不精確。kline_daily 的 33 列拿 OHLCV 與成交量逐筆比對，全部等於前一交易日的官方 K，只是被標成下一個日期：分區 9/16 是官方 9/15，9/17 是 9/16，9/18 是 9/17，9/22 是 9/21。9/16 到 9/18 三個分區的 quote_ts 換算成台北時間，都是前一交易日 13:30。這是 B3 修正前留下的汙染：B3 修正 commit f43c7bd 在 2026-09-22 19:36，最後一個分區的 mtime 是 9/22 19:16。B3 的修法只擋新的寫入，沒有清理舊分區。所以回退用的歷史不只稀疏，日期也整體錯了一個交易日。0050 的 MA5＝(9/15、9/16、9/17、9/21 的收盤＋9/23)/5＝109.0，實際缺的是 9/18 與 9/22。
(2) 「預熱的數十秒」誇大了。log 裡預熱耗時最長 13.6 秒，三份 log 的平均分別是 3.56／3.76／2.06 秒；通常只有啟動後第一輪輪詢會碰到，最多 2 到 3 輪。

我另外查到一個 finding 沒提的面向，未做實際重現：回退列經 merge_live_enriched_asset 寫進 _enriched_cache（repository.py:2255、2303），下一輪 compute_enriched_today 會拿它當 prev_enriched 判斷交叉（pipeline.py:1626 起）。用 9/23 收盤模擬，00885 回退列的 DIF 0.0183 大於 DEA 0.0107，正確值是 0.0172 小於 0.0533，下一輪會出現一次假的 MACD 死叉。磁碟上的正確資料 9/22 與 9/23 都沒有交叉。另外，MA 與 MACD 不在 ENRICHED_STORAGE_COLS 內，錯值不會寫進磁碟。使用者目前的監控規則是 0 條，不會觸發告警。整體影響短暫、只存在記憶體裡，也會自己恢復，所以維持 P3。
    - 證據：
      - backend/app/services/quote_service.py:1708-1713：use_incremental 需要 asset_type 為 stock，而且 live_agg、prev_enriched、prev_date 都不能是空的
      - backend/app/services/quote_service.py:1729-1743：cutoff＝today−90 天，table＝kline_daily，並排除 date==today 的列
      - backend/app/repository.py:1249-1251：預熱中 get_live_agg 回傳 pl.DataFrame()
      - backend/app/taiwan/enriched_refresh.py:3-5：docstring 寫「The indicator pipeline only sources kline_daily, which the Taiwan deployment never populates」
      - data/adj_factor 與 data\instruments_etf 都是空目錄，所以 factors 為空，0050、00878 等 ETF 被歸為 stock，走 kline_daily
      - 重現腳本 AUDIT_SCRATCH/work/verify-a02fb/repro.py，執行 `cd backend && uv run python repro.py`，結果寫到 repro_out.csv
      - 2454.TWSE：回退 ma5/ma20/ma60 = null/null/null；TaiwanDailyStore 全歷史重算＝磁碟 9/23 分區＝4917.0/4531.25/4067.166667；官方 STOCK_DAY 算出 MA5=4917.0、MA20=4531.25（finding：null vs 4917／4531.25／4067.17，一致）
      - 2454.TWSE：回退 macd_dif=80.4148、macd_dea=21.6075；正確 262.749184／206.134798（finding：80.41 vs 262.75，一致）
      - 0050.TWSE：回退 ma5=109.0、ma20/ma60=null；正確 ma5=110.71（官方收盤算也是 110.71）、ma20=108.4125、ma60=105.3475（finding：109.0 vs 110.71，一致）
      - 其他自選股的回退 MA5 偏差：00893 43.568 vs 44.558、3545 50.36 vs 51.42、6196 527.8 vs 535.0；2379、3034 的 MA5 為 null。九檔的 ma20 與 ma60 全部是 null
      - TaiwanDailyStore 與官方 twse_stock_day_202608_202609.csv 收盤價比對：342 列，0 筆不符；兩邊在 8/3 到 9/23 都是每檔 38 天
      - shift_check.py：kline_daily 的 33 列全部以 OHLCV＋成交量對到官方 K，日期落差一律是 1 個交易日（分區 09-16→官方 09-15、09-17→09-16、09-18→09-17、09-22→09-21）；日期與官方同一天且收盤相同的只有 1/33 列。9/16～9/18 分區的 quote_ts 換成台北時間都是前一日 13:30
      - git log -S _records_datable_today：B3 修正 commit f43c7bd 在 2026-09-22 19:36 +0800；kline_daily/date=2026-09-22/part.parquet 的 mtime 是 9/22 19:16，早於修正
      - data/backend.log*：共 166 筆 fallback（log.3 71 筆、log.1 65 筆、log 30 筆），全部在 app ready 後 15 秒內；預熱最長 13.6 秒，平均 2.06～3.76 秒。真實重啟 backend.log.1：09:17:08 走全量回退，09:17:17 回到增量
      - backend/app/repository.py:2255,2303：回退列會併進 _enriched_cache；backend/app/indicators/pipeline.py:1626 起，下一輪用 prev_enriched 的 ma5/ma20/ma60/macd_dif/macd_dea 判斷交叉訊號。以 9/23 收盤模擬，00885 回退 DIF 0.0183 > DEA 0.0107，正確 0.0172 < 0.0533，因此下一輪會出現一次假的 MACD 死叉；磁碟上 9/22、9/23 的 signal_macd_dead 都是 False
      - backend/app/indicators/pipeline.py:92-101：ENRICHED_STORAGE_COLS 不含 MA 與 MACD，錯值不會落盤

### [P3 → P3] 無成交日（價格為 null 的列）在不同路徑處理不同：自選頁與選股器的 MA 會變 null 最多 60 天（確認成立）

- **主張**：全量 pipeline 的 filter_halt_days 對 open/high 為 null 的列，因比較結果為 null 而把整列濾掉，所以 MA 只計實際有成交的日子。technical_indicators.py 與台股選股器則直接對含 null 的歷史做 rolling_mean(min_samples=n)，視窗內只要有一天無成交，MA_n 就是 null，持續 n 個交易日。
- **正確的定義或數值**：三條路徑對無成交日採用同一種規則（跳過該日，或以前收補值）。
- **app 實際行為**：enriched 跳過無成交日；自選頁與選股器則讓 MA 變成 null。
- **對使用者的影響**：冷門股與 ETF 在自選頁與選股器上的均線時有時無，與 K 線圖上的均線不一致；自選 9 檔目前不受影響（最近 60 天沒有 null 列）。
- **修法建議**：technical_indicators 與選股器讀取後先套用 filter_halt_days，或至少 drop_nulls('close')，與 pipeline 一致。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:978-991（filter_halt_days）
  - backend/app/taiwan/technical_indicators.py:75-82（沒有過濾 null 列）
  - backend/app/taiwan/screener.py:305-311、515-523（沒有過濾；close > ma5 遇到 null 時整列被篩掉）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - a02_watch.out [N]：3545 截至 2026-03-31（窗內含 2026-02-26 的 null 列），自選頁路徑 ma60＝null，enriched 的 ma60＝50.348。
  - a02_nulls.py：全店有 12,351 列 null 收盤，分屬 541 檔；最近 60 個交易日（2026-07-01 起）有 1,499 列、245 檔，這些標的在自選頁的 MA60 目前顯示「—」，選股器「站上 MA5／MA20」會靜默排除它們（在 MA5／MA20 視窗內者）。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：原敘述成立，數字全部重現。

台股日K中收盤為 null 的列（open、high、low 同時為 null），對應官方沒有收盤價的無成交日或暫停交易日。例如 3545 在 2026-02-26 因重大訊息停牌；2026-09-23 的 7 筆 null 列與證交所 MI_INDEX 逐筆一致。

- enriched 路徑：filter_halt_days 的比較結果為 null，整列被丟掉，MA 只算有成交的日子。
- 自選頁路徑：technical_indicators.py:79-82 直接對含 null 的歷史做 rolling_mean(min_samples=n)，視窗內有一天無成交，MA_n 就持續 n 個交易列為 null。3545 截至 2026-03-31 的 MA60 為 null，K 線／enriched 為 50.348。
- 選股器路徑：screener.py:309-311 的 rolling_mean 用預設 min_samples，結果相同。

全店有 12,351 列、541 檔受影響；最近 60 個分區有 1,499 列、245 檔，這 245 檔在自選頁路徑的 MA60 全部為 null，K 線全部有值。選股器目前有 43 檔（MA5）和 141 檔（MA20）不論選「站上」或「跌破」都會被靜默排除。

另外，自選頁路徑的 momentum_5d／20d 也依列位移，會把無成交列算進去，9/23 有 11／29 檔與 enriched 不一致。自選 9 檔目前不受影響。自選頁的 MA 欄預設隱藏，因此對使用者的實際影響有限，維持 P3。
    - 理由：我用自己寫的腳本（直接用 polars 讀 parquet，只呼叫純計算函式 compute_taiwan_daily_indicators、compute_enriched、filter_halt_days），重算 finding 引用的所有數字，結果全部一致。

1. 全店資料：661 個分區，共 1,435,948 列。收盤為 null 的有 12,351 列，分屬 541 檔。最近 60 個分區從 2026-07-01 起算，其中 null 收盤 1,499 列、245 檔。這些數字與 finding 相同。
2. null 列的欄位型態：12,351 列的 open、high、low 全部是 null。沒有任何一列是 open==0 且 high==0，也沒有收盤有值但 open 或 high 為 null 的列。filter_halt_days 剛好濾掉 12,351 列，濾完後不剩任何 null 收盤列。這證實 finding 的說法：比較結果為 null 時，Polars 的 filter 會把整列丟掉，所以無成交列只在這條路徑被濾除。
3. 3545 截至 2026-03-31：
   - 自選頁路徑（取 150 個分區，compute_taiwan_daily_indicators）算出 ma60＝null。
   - 用純函式 compute_enriched 重算，以及直接讀磁碟上的 enriched 分區，ma60 都是 50.348333。
   - 手算「最近 60 個有收盤價的交易日」平均也是 50.34833，起算日為 2025-12-23。
   - 自選頁路徑的 60 列視窗從 2025-12-24 起，只有 59 個非 null 值。
   - ma60 在自選頁路徑連續 60 個交易列為 null（2026-02-26 至 2026-05-26），吻合「最多 60 天」。
4. 這些 null 列確實是官方的無成交或停牌日，不是資料缺漏：
   - 2026-09-23 的 7 筆 null 收盤，在官方 MI_INDEX 參考資料裡收盤價全部是空的（無成交價），成交股數也逐筆相同。其中 3 筆有零星成交量（999、167、10 股），但官方同樣沒有收盤價。
   - 3545 在 2026-02-26 的那一列 volume=0。經濟日報與 CMoney 都報導敦泰因重大訊息待公布，2/26 起暫停交易，3/2 恢復。
5. 影響面（新量化）：
   - 自選頁路徑：245 檔全部 ma60＝null，而它們 enriched 最新一列的 ma60 全部有值（220 檔股票、25 檔 ETF）。以各檔最新一列計，MA5、MA20、MA60 分別有 71、169、247 檔出現「自選頁 null、K 線有值」。
   - 選股器路徑（35 個分區，rolling_mean 用預設 min_samples）：最新收盤有值、但 MA5 為 null 的有 43 檔，其中依 enriched 應該站上 MA5 的 21 檔、在下方的 22 檔。MA20 為 null 的有 141 檔（站上 54 檔、在下方 87 檔）。這些標的不論選 above=True 或 False 都會被濾掉，實測保留 0 列。
   - 自選 9 檔在最近 60 個分區都沒有 null。自選頁 MA5、MA20、MA60 與 enriched 完全相同，因此目前不受影響。
6. finding 沒提到的面向：自選頁路徑的 momentum_5d／momentum_20d 也用 shift(n) 依「列」位移，會把無成交列算進去。2026-09-23 的 1,323 檔上市股中，分別有 11 檔與 29 檔的值或 null 狀態與 enriched 不一致。「原因是 null 列」這一點，是從兩條路徑公式相同、唯一差別是 null 列推得的（推測，但有程式碼依據）。RSI／MACD 也有差異，不過沒有 null 的 0050 也不一致，主因是 150 分區的暖機截斷，和本 finding 無關。
7. 使用者看得到的影響：前端 fmtMaybePrice 把 null 顯示成「—」。不過自選頁的 MA5／MA10／MA20／MA60 欄位預設都是隱藏（visible: false），所以實際影響比 finding 描述的小。維持 P3。另外查過 docs/code-review-2026-09-22.md，沒有記錄這個問題。
    - 證據：
      - backend/app/indicators/pipeline.py:978-988：filter_halt_days 只保留 ~((open==0)&(high==0)) 為 True 的列；open 或 high 為 null 時結果為 null，列被濾掉。pipeline.py:1010 的 compute_enriched 會先呼叫它
      - backend/app/taiwan/technical_indicators.py:79-82：rolling_mean(n, min_samples=n)，前面沒有任何過濾 null 的步驟
      - backend/app/taiwan/daily_store.py:224：read_range 只濾掉 date 為 null 的列，不過濾 close 為 null 的列；watchlist_enrichment.py:153-159 取 150 個分區後直接餵給 compute_taiwan_daily_indicators
      - backend/app/taiwan/screener.py:309-311：rolling_mean(5/10/20) 未指定 min_samples（實測 polars 1.40.1 的預設行為是視窗內有 null 就回傳 null）；screener.py:516-523 的 close>ma 與 close<=ma 遇到 null 都會把列濾掉
      - v1.out（verify-halt-null-ma\v1.py）：null 收盤 12,351 列／541 檔；2026-07-01 起 1,499 列／245 檔；null 列的 open、high、low 全為 null（6,666 列 volume>0、5,685 列 volume=0）；open==0&high==0 的列數為 0；filter_halt_days 濾掉 12,351 列
      - v1.out：3545 截至 2026-03-31，自選頁路徑 ma60=null，compute_enriched 與磁碟 enriched 的 ma60=50.348333，手算最近 60 個非 null 收盤平均=50.34833；自選頁路徑 ma60 null 期間為 2026-02-26～2026-05-26，共 60 列
      - v2.out：2026-09-23 的 7 筆 null 收盤與官方 reference/csv/twse_mi_index_20260923_quotes.csv 比對，7/7 官方都沒有收盤價，3 筆有量者的成交股數相同；3545 在 2026-02-26 那一列 OHLC 為 null、volume=0，2026-03-02 open=47.35、close=52.6
      - 線上：https://money.udn.com/money/story/5710/9344605 與 https://cmnews.com.tw/article/newsyoudeservetoknow-40f3d7bb-1244-11f1-a33d-245b9abaf57b，搜尋摘要：「敦泰因有重大訊息待公布，相關上市普通股自26日起暫停交易」、「自2026年3月2日起恢復交易」
      - v5.out：在 null 集中於最近 60 個分區的 245 檔中，自選頁路徑 ma60 全為 null、enriched 全有值（stock 220、etf 25）；以各檔最新一列計，「自選頁 null、enriched 有值」的 MA5／MA20／MA60 為 71／169／247 檔
      - v6.out：選股器路徑 MA5 為 null、enriched 有值的有 43 檔（應站上 21、在下方 22），MA20 有 141 檔（54／87），close>ma 與 close<=ma 都保留 0 列
      - v3.out：自選 9 檔的 ma5、ma20、ma60 與 enriched 完全相同，最近 60 個分區沒有 null（3545 在 150 分區內只有 2026-02-26 這一筆 null）
      - v4.out：2026-09-23 的 1,323 檔上市股中，自選頁路徑的 momentum_5d 有 11 檔、momentum_20d 有 29 檔與 enriched 不一致（新面向）
      - frontend/src/components/stock-table/primitives.tsx:38-39：null 顯示為「—」；frontend\src\lib\watchlist-columns.ts:40-43：MA5、MA10、MA20、MA60 欄位預設 visible: false
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-halt-null-ma/v1.py～v6.py 與 v1.out～v6.out

### [P1 → P1] 台股 enriched 用未還原價：分割與除權後 MA／EMA／MACD 大幅失真並觸發假訊號（部分成立）

- **主張**：台股 enriched 計算時傳入空的復權因子，所以 open/high/low/close 就是原始價；股票分割、大額股票股利與除息都直接進入 MA、EMA、MACD 與交叉訊號。這違反 CONTRIBUTING 3.2「enriched 的 open/high/low/close 為前復權價格」，也與 3.2「技術指標…不得混用」的精神衝突。
- **正確的定義或數值**：MA、EMA、MACD 與訊號以前復權價計算（或至少處理分割與股票股利），原始價只用於漲跌停判斷，符合 CONTRIBUTING 3.2。
- **app 實際行為**：全部用未還原價。自選股 0050 的 2025 年 6～9 月均線與 MACD 失真，並有多個假訊號；6669 目前（2026-09-23）的 MA60 是收盤價的 2.3 倍，N 日新低訊號接連誤觸發；一般除息日也會出現假的跌破均線訊號。
- **對使用者的影響**：看 0050 的 2025 年日K時，副圖 MACD 與均線會出現斷崖；任何以 MA、MACD 或 N 日新低為條件的策略、選股與回測，在分割、除權附近會誤判（推測：跨 2025-06-18 的 0050 回測會出現約 −75% 的假虧損）。6669 這類近期除權股目前在選股器與策略裡仍呈現假空頭。
- **修法建議**：從 TWT49U（除權息參考價）與分割、面額變更、減資公告建立台股復權因子檔，傳進 compute_enriched 的 factors；至少要偵測 |close/prev−1| 超過法定漲跌幅上限的公司行動，改用官方參考價調整。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
  - backend/app/indicators/pipeline.py:1022-1027（只在 factors 非空時才套前復權）
  - CONTRIBUTING.md:100 「enriched 的 open/high/low/close 為前復權價格」
  - 整個 backend/app/taiwan 都搜不到 split、還原或 adj_factor 的處理（grep -i）
- **線上證據**：
  - https://finance.technews.tw/2025/06/10/one-split-into-four/ 與 https://money.udn.com/money/story/124512/8553991 ：0050 一拆四，6/10 最後交易、6/11～6/17 暫停交易、6/18 恢復，恢復參考價 47.16 元
  - 官方 TWT49U（參考資料 twse_twt49u_20260601_20260923.csv）：6669 緯穎 2026-09-02「權」，除權前收盤 7800、除權參考價 2614.99；00878 在 2026-08-18 除息 1.01（前收 33.75）；6196 在 2026-06-24 除息 6.4488
- **本地計算證據**：
  - a02_adj.out：0050 在 2025-06-18 close 47.57（前一日 188.65），ma60＝171.95（為收盤的 3.61 倍），macd_dif 在 7 月初最低到 −35.8；當日同時觸發 ma5_breakdown、ma20_breakdown、ma_dead_5_20、macd_dead、n_day_low；之後再觸發假的 macd_golden（2025-07-08）、golden_5_20（2025-07-15、2025-08-27）、golden_20_60（2025-09-09）。店內 0050 缺 6/11～6/17 五天，與暫停交易期間一致。
  - a02_6669.py：6669 在 2026-09-23 close 2105、ma20 3288.25、ma60 4860.17、DIF −927.4；signal_n_day_low 從 9/2 到 9/23 的 16 個交易日中有 10 天觸發。
  - a02_adj.out 除息日比對（以 TWT49U 參考價向前還原後重算）：6196 在 2026-06-24 原始價觸發 ma10_breakdown、還原後不觸發；00878 在 2026-08-18 原始價觸發 ma20_breakdown、還原後不觸發。
  - a02_gaps.out：全店 2024–2026 年有 43 筆「收盤／前收 <0.5 或 >2」的跳空，分屬 40 檔（分割、減資、反分割候選）。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 的計算固定傳入空的復權因子（enriched_refresh.py:95），所以 pipeline.py:1023 的前復權永遠不會執行，open/high/low/close 就是原始價（daily_store.py:41 也宣告為 RAW）。這違反 CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」。

因此股票分割、大額股票股利與除息都直接進入 MA、EMA、MACD、60 日新高與新低，以及各種交叉訊號：
- 0050 在 2025-06-18 一拆四當天，觸發 ma5、ma10、ma20 跌破、死叉、macd_dead 與 n_day_low 等假訊號；之後又出現 2025-07-08 的 macd_golden、2025-07-15 的 golden_5_20、2025-09-09 的 golden_20_60 等假訊號。
- 更正：2025-08-27 的 golden_5_20 在還原後仍然出現，是真訊號，不應列為假訊號。
- 6669 在 2026-09-02 除權後，9/2～9/23 的 n_day_low 有 10 次假觸發（還原後是 0 次）；9/23 的 MA60 是 4860，約為收盤價的 2.3 倍（還原後是 2038）。
- 一般除息日也會產生假的跌破均線訊號，例如 6196 在 2026-06-24、00878 在 2026-08-18。
- 另外，台股選股器（screener.py:299-312）自己用原始價計算 MA、change_pct 與 RSI，同樣沒有還原。

在使用者情境下，0050、00878、6196 的歷史圖表、除息日訊號與回測都會受影響；不過監控規則是 0 條，不會產生假告警，而 0050 分割的影響在目前的指標值裡已經消退。
    - 理由：finding 的核心主張成立，嚴重度維持 P1，只有一處細節要更正：finding 列出的 0050 假訊號裡，2025-08-27 的 golden_5_20 其實是真訊號。

一、程式碼（親自讀過，行號仍正確）
- 台股 enriched 固定傳入空的復權因子：backend/app/taiwan/enriched_refresh.py:95 呼叫 `compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`。
- 前復權只在有因子時才執行：backend/app/indicators/pipeline.py:1023-1024 的 `if factors is not None and not factors.is_empty(): raw = _apply_adj_factor(raw, factors)`。
- 資料來源本身就宣告為原始價：backend/app/taiwan/daily_store.py:41 的 `price_semantics=PriceSemantics.RAW`。
- 違反的契約：CONTRIBUTING.md:100 規定「enriched 的 open/high/low/close 為前復權價格」。
- `backend/app/taiwan` 底下只有 dividend_events.py 出現「除權」字樣，而它只被 fundamentals.py:304 用來處理股利資料，不參與價格還原。所以 finding 說「沒有任何還原處理」大致正確。
- 已知問題清單 docs/code-review-2026-09-22.md 沒有任何「復權、還原、除權」相關條目，不算重複回報。

二、正確定義：線上來源，與 finding 引用的不同
- TEJ（台灣經濟新報）說明分割或除權息落差會讓股價看起來「跳空」，導致錯誤的技術分析判斷。還原方式是把歷史股價乘上「除權息參考價 ÷ 前日收盤價」的累乘，並註明分割與增減資也要調整。
- Yahoo Finance 說明頁把 adjusted close 定義為扣除所有分割與股利影響後的收盤價，例如一拆四之前的價格要乘 0.25。
- 兩者都支持「均線、MACD 這類技術指標應該用還原價計算」，與 CONTRIBUTING 3.2 的契約一致。

三、本地重現（只讀 parquet，並用純計算函式重算）
- 已儲存的 enriched 資料完全符合 finding 的數字：
  - close 等於 raw_close。
  - 0050 在 2025-06-18：close 47.57，ma60 171.95，這段期間 DIF 最低 −35.81。
  - 6669 在 2026-09-23：close 2105，ma20 3288.25，ma60 4860.17，DIF −927.43。
  - 6669 從 9/2 到 9/23 的 16 個交易日中，n_day_low 觸發 10 天。
- 我用 TWT49U 的官方參考價與 0050 一拆四的比例（188.65/47.16）建立因子，再以 compute_enriched 重算並比對：
  - **6669**：還原後 n_day_low 是 0/16，10 次全是假訊號；9/2 的 ma5、ma10、ma20 跌破、macd_dead 與 n_day_low 也只在未還原時出現。還原後 9/23 的 ma60 是 2037.7，DIF 是 −6.9。
  - **0050 在 2025-06-18**：未還原才出現的訊號有 ma5、ma10、ma20 跌破，ma_dead_5_20，macd_dead，n_day_low，boll 下軌跌破。還原後同一天反而是 n_day_high。
  - **0050 後續訊號**：未還原才出現的有 7/08 macd_golden、7/15 golden_5_20、9/09 golden_20_60。**2025-08-27 的 golden_5_20 在還原後仍然出現**（那時 MA20 的計算窗口已經全在分割之後），所以 finding 把它列為假訊號是錯的。
  - **一般除息**：6196 在 2026-06-24 的 ma10_breakdown、00878 在 2026-08-18 的 ma20_breakdown，都只在未還原時出現。

四、使用者情境（自選 9 檔、監控規則 0 條）
- 0050、00878、6196 都在自選清單內，會實際受影響：
  - 0050 在 2025 年 6～9 月的均線與 MACD 出現斷崖。
  - 00878 每季除息日都可能出現假的跌破訊號。
- 因為監控規則是 0 條，這些假訊號不會觸發推播告警。
- 0050 的分割已過去約 15 個月，重算後 2026-09-23 的 ma60 是 105.35（未還原）對 105.21（還原），目前的即時訊號幾乎不受影響；受影響的是歷史圖表、回測，以及近期有除權息的股票，例如 6669。
- 另外發現一個原 finding 沒提到的面向：台股選股器 backend/app/taiwan/screener.py:299-312 會自己從原始日K計算 change_pct、ma5、ma10、ma20、momentum_5d 與 RSI，同樣沒有還原。所以選股器的 close 對 ma20 條件（screener.py:521-523）也會把 6669 判成空頭。

五、嚴重度
- 這屬於「錯誤的金融口徑」，也違反明文契約，所以是 P1。
- 沒有到 P0：資料本身沒有被破壞，而回測出現約 −75% 假虧損這一點，finding 自己也標成推測，我沒有驗證回測引擎實際用哪個價格計算報酬。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 `enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`
      - backend/app/indicators/pipeline.py:1023-1024 只有在 factors 非空時才呼叫 _apply_adj_factor；pipeline.py:237-300 的 _apply_adj_factor 在因子為空時直接回傳原資料
      - backend/app/taiwan/daily_store.py:41 `price_semantics=PriceSemantics.RAW`
      - CONTRIBUTING.md:100 「enriched 的 `open/high/low/close` 為前復權價格。」
      - backend/app/indicators/pipeline.py:438-440 high_60d／low_60d 是 close 的 rolling 60 日極值；pipeline.py:656 signal_n_day_low = close <= low_60d
      - backend/app/taiwan/screener.py:299-312 選股器用 daily_store 原始價自己計算 change_pct、ma5、ma10、ma20、momentum_5d（原 finding 未提到的面向）
      - 線上來源：https://www.tejwin.com/insight/%E9%82%84%E5%8E%9F%E8%82%A1%E5%83%B9/ 原文「無論是畫出K線圖、分析乖離率，或是計算股價波動率等統計數據，若中間存在除權息落差或分割事件，容易視覺上出現股價「跳空」的錯覺，因而做出錯誤的技術分析判斷」；「調整係數：除權息參考價 ÷前日收盤價」；「實際調整係數可能包含股票分割及增減資較為複雜」
      - 線上來源：https://help.yahoo.com/kb/finance/sln28256.html 原文 "Adjusted close is the closing price after adjustments for all applicable splits and dividend distributions."；"In a 4 for 1 split, the pre-split data is multiplied by 0.25."
      - 官方參考資料 twse_twt49u_20260601_20260923.csv：6669 2026-09-02 權 7800.0→2614.99；6669 2026-06-22 息 5130.0→4985.6；6196 2026-06-24 息 541.0→534.55；00878 2026-08-18 息 33.75→32.74；0050 2026-07-21 息 99.2→98.6
      - 本地重現（已存的 enriched parquet，腳本 scratchpad\audit\work\v_a02_defcode\check_stored.py）：0050 2025-06-10 close 188.65；2025-06-18 close＝raw_close＝47.57、ma60＝171.952833、macd_dif＝−8.24；06-11～06-17 沒有資料；這段期間 macd_dif 最小值 −35.807896；6669 2026-09-23 close 2105、ma20 3288.25、ma60 4860.166667、dif −927.429356
      - 本地重算（純函式 compute_enriched，腳本 scratchpad\audit\work\v_a02_defcode\recompute.py）：6669 在 9/2～9/23 的 n_day_low 未還原 10/16、還原 0/16；0050 2025-06-18 未還原才出現的訊號為 boll_breakdown_lower、ma10/ma20/ma5_breakdown、ma_dead_5_20、macd_dead、n_day_low，還原後當天是 n_day_high
      - 本地重算：0050 golden_5_20 未還原為 [06-09, 07-15, 08-27, 09-09]、還原為 [06-09, 08-27, 09-09]，所以 2025-08-27 是真訊號，finding 列錯；golden_20_60 未還原為 [09-09]、還原為 []；macd_golden 在 07-08 只有未還原時出現
      - 本地重算：6196 2026-06-24 的 ma10_breakdown 只有未還原時出現；00878 2026-08-18 的 ma20_breakdown 只有未還原時出現（ma5 與 ma10 跌破在兩種算法都出現）
      - 本地重算 2026-09-23 的現值：0050 ma60 未還原 105.3475 對還原 105.2089（分割影響已消退）；6669 ma60 4860.17 對 2037.72、dif −927.43 對 −6.92
      - docs/code-review-2026-09-22.md 搜尋 復權、复权、還原、除權、split 都沒有相關條目（只有 549 行的 lazy 分割，無關），所以這不是已知問題
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 以空的復權因子呼叫 compute_enriched（enriched_refresh.py:95），所以 open/high/low/close 都是未還原價。台股 provider 也明文採原始價：hybrid_provider.py:31 設 adj_factor=False，:144-151 註明「Phase 2 records raw price semantics」。這是刻意延後的設計，但違反 CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」，docs 也沒有記錄這個例外。

後果一：分割、減資、反分割與大額除權直接扭曲 MA、EMA、MACD 與 N 日高低點。全店 2024-01～2026-09 有 43 筆、40 檔的 >2 倍或 <0.5 倍跳空。
- 自選股 0050 在 2025-06-18 一拆四（188.65→參考價 47.16，收盤 47.57）。當天 ma60 為 171.95（收盤的 3.61 倍），同時假觸發 ma5、ma10、ma20 breakdown，以及 ma_dead_5_20、macd_dead、n_day_low。
- 之後的假訊號有 macd_golden（7/08；還原後同日其實是 macd_dead）、golden_5_20（7/15）、golden_20_60（9/09）。golden_5_20 的 8/27 是真訊號，不算假訊號。
- 同一段期間也有漏報：還原後應有的 21 次 n_day_high（6/18～9/08）與多次 MACD 交叉，在原始價中消失。DIF 最低 -35.81 出現在 2025-06-30。
- 6669 在 2026-09-02 除權（7800→2614.99）。2026-09-23 的 ma20 為 3288.25、ma60 為 4860.17（收盤 2105 的 2.31 倍）、DIF 為 -927.4；9/02 起 16 天中 n_day_low 假觸發 10 天。還原後 ma60 為 2037.7、DIF 為 -6.9，n_day_low 0 天。

後果二：一般除息日偶爾會出現假的跌破均線訊號。自選股 2026-06～09 的 6 筆除權息事件中，2 筆有假訊號（6196 6/24 的 ma10_breakdown、00878 8/18 的 ma20_breakdown），其餘 4 筆沒有差異。

跨分割的回測損益：以原始價計算為 -74.8%，但回測引擎是否以 close 計算持倉損益未驗證，仍屬推測。
    - 理由：核心問題成立：台股 enriched 用的是未還原價。finding 列的關鍵數值我幾乎全部精確重現。我用兩支腳本 repro.py 與 gaps.py，以本地 parquet 加官方 TWT49U 參考價重算。先確認店內 enriched 與「空因子 compute_enriched 重算」逐列相同（2639 列，close、ma60、macd_dif 最大差 0.0），且 close==raw_close 全部成立。再以官方參考價建前復權因子重算，逐項對照如下：
(1) 0050 2025-06-18：close 47.57、前收 188.65、ma60 171.9528，是收盤的 3.6147 倍，DIF -8.24。原始價當天觸發 ma5、ma10、ma20 breakdown，以及 ma_dead_5_20、macd_dead、n_day_low；還原後這些都不觸發，反而觸發 n_day_high。
(2) 0050 缺 6/11～6/17 五天：店內有這 5 個分區，但沒有 0050 的列，與中央社報導的暫停交易期間一致。
(3) 6669 2026-09-23：close 2105、ma20 3288.25、ma60 4860.17（2.309 倍）、DIF -927.43，與 finding 完全相同。9/2 起 16 個交易日中 n_day_low 觸發 10 天；還原後 ma60 2037.7、DIF -6.9、n_day_low 0 天。
(4) 除息日：6196 在 6/24 的 ma10_breakdown 原始價為 True、還原後為 False；00878 在 8/18 的 ma20_breakdown 原始價為 True、還原後為 False。兩者都重現。
(5) 全店收盤／前收 <0.5 或 >2 的跳空：以相鄰列計算與以店內 prev_close 欄計算兩種方法，都得到 43 筆、40 檔。

與 finding 不符或需要修正的地方：
(a) finding 說 golden_5_20 在 2025-08-27 是假訊號，不成立。原始價的 golden_5_20 在 7/15、8/27、9/09 觸發，還原後在 8/27、9/09 觸發，所以只有 7/15 是假的，8/27 是真訊號。
(b) DIF 最低點 -35.81 出現在 2025-06-30，不是「7 月初」（7/1 為 -35.47），差異輕微。
(c) finding 說「整個 backend/app/taiwan 都搜不到 adj_factor 處理」，不精確。程式碼明文宣告台股採原始價：hybrid_provider.py:31 設 adj_factor=False；:144-151 的 get_adj_factors 回空表，docstring 寫「Phase 2 records raw price semantics」；daily_store.py:41 為 PriceSemantics.RAW。所以這是刻意延後、而非遺漏，但仍違反 CONTRIBUTING 3.2，docs 也沒有記錄這個例外。
(d) 未還原價不只造成假訊號，也造成訊號漏報與反向訊號，finding 低估了這部分。0050 在 6/18～9/08 還原後應有 21 次 n_day_high，原始價全部漏報（原始價要到 9/09 才恢復）。7/08 原始價給 macd_golden，還原後同一天是 macd_dead，方向相反。還原後的 MACD 交叉（golden 7/11、7/15、8/11、9/09；dead 7/08、7/14、7/25、8/20）在原始價中幾乎全部消失。
(e) 自選股 TWT49U 除權息事件（2026-06～09）：有 K 棒的 6 筆中，2 筆會翻轉訊號（6196、00878），4 筆沒有差異（2454、0050 2026-07-21、3545、2379）。所以除息日「會」出現假跌破，但不是每次。3034 的 7/10 沒有 K 棒（店內無 2026-07-10 分區）。

finding 的「約 -75% 假虧損」：算術上 47.57/188.65-1 = -74.8% 成立，但我沒有驗證回測引擎是否以 close 計算持倉損益，維持「推測」。

嚴重度：違反 CONTRIBUTING 3.2 的價格口徑，並影響自選股 0050 與全店 40 檔的指標與訊號，維持 P1。
    - 證據：
      - 程式碼 backend/app/taiwan/enriched_refresh.py:95：compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - 程式碼 backend/app/indicators/pipeline.py:1022-1023：只在 factors 非空時才呼叫 _apply_adj_factor；:237-302 的前復權公式為 adjusted = raw × cum/total
      - 規範 CONTRIBUTING.md:100：「enriched 的 `open/high/low/close` 為前復權價格。」
      - 程式碼（修正 finding 的 grep 說法）backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:144-151 get_adj_factors 回傳 pl.DataFrame()，docstring「Return empty adjustment factors (Phase 2 records raw price semantics)」；app\taiwan\daily_store.py:41 price_semantics=PriceSemantics.RAW
      - 本地重算指令：cd backend && uv run python ...\scratchpad\audit\work\verify-adj\repro.py，輸出在 repro.out。店內 enriched 與空因子重算相比 2639 列，close、ma60、macd_dif 最大差 0.0；stored close==raw_close 全部為 True
      - 0050 2025-06-18（店內）：close 47.57、prev_close 188.65、ma5 156.704、ma20 174.6185、ma60 171.952833（3.6147 倍）、macd_dif -8.2415；ma5、ma20 breakdown，以及 ma_dead_5_20、macd_dead、n_day_low 全為 True（ma10_breakdown 也為 True）
      - 0050 以官方參考價 47.16 還原（因子 188.65/47.16=4.000212）後的 2025-06-18：close 47.28、ma5 46.03、ma20 45.16、ma60 43.32、DIF 0.78；所有 breakdown、dead、n_day_low 都為 False，n_day_high 為 True
      - 0050 原始價 vs 還原價，2025-06-18～09-30：golden_5_20 原始價在 7/15、8/27、9/09，還原價在 8/27、9/09，所以 8/27 不是假訊號；golden_20_60 原始價在 9/09，還原價無；macd_golden 原始價只有 7/08，還原價在 7/11、7/15、8/11、9/09；macd_dead 原始價只有 6/18，還原價在 7/08、7/14、7/25、8/20；n_day_low 原始價在 6/18～6/23 觸發 4 天，還原價 0 天；n_day_high 原始價到 9/09 才開始，還原價在 6/18～9/08 有 21 天
      - 0050 macd_dif 最低值：2025-06-30 為 -35.8079，2025-07-01 為 -35.4669
      - 0050 原始日K 的日期：6/9、6/10、6/18、6/19；店內分區 2025-06-11、06-12、06-13、06-16、06-17 都存在，但沒有 0050 列
      - 6669 2026-09-23（店內）：close 2105、ma20 3288.25、ma60 4860.166667、macd_dif -927.429；9/02～9/23 共 16 個交易日，n_day_low 觸發 10 天。還原後（因子 7800/2614.99 與 5130/4985.6）：ma20 2327.36、ma60 2037.72、DIF -6.92，n_day_low 0 天
      - 官方 TWT49U（reference\csv\twse_twt49u_20260601_20260923.csv）：6669 2026-09-02 權，前收 7800.0，參考價 2614.99；6196 2026-06-24 息，541.0→534.55；00878 2026-08-18 息，33.75→32.74
      - 6196 2026-06-24：原始價 close 540、ma10 541.7、ma10_breakdown=True；還原後 ma10 535.885、ma10_breakdown=False。00878 2026-08-18：原始價 close 32.38、ma20 32.7155、ma20_breakdown=True；還原後 ma20 31.7849、ma20_breakdown=False（ma5、ma10 breakdown 兩者都為 True）
      - gaps.py（輸出 gaps.out）：全店 661 個分區、1,435,948 列、2346 檔；收盤／前收 <0.5 或 >2，以相鄰列計算得 43 筆、40 檔，以店內 enriched prev_close 欄計算同為 43 筆、40 檔
      - 自選股 TWT49U 事件逐一比較：6196（6/24）與 00878（8/18）有訊號翻轉；2454（7/07）、0050（2026-07-21）、3545（8/04）、2379（9/08）沒有差異；3034（7/10）無 K 棒（店內無 2026-07-10 分區）
      - 線上：https://www.cna.com.tw/news/afe/202506180032.aspx 原文「股價從6月10日分割前的188.65元降為47.16元」「決定以4倍進行分割」
      - 線上：https://finance.technews.tw/2025/06/10/one-split-into-four/ 原文「預計自 6 月 11 日至 6 月 17 日暫停交易」「並在 6 月 18 日恢復交易」
      - WebSearch 摘要（今周刊 https://www.businesstoday.com.tw/article/category/183013/post/202506180032/ 標題）：「0050分割恢復交易，現在47.57元」，與店內 6/18 收盤 47.57 一致

### [P2 → P2] 盤中增量 live_agg 用 90 個日曆天代替 60 個交易日：2–5 月 MA60 低估 7–9%，新上市股的 MA 算出錯值（部分成立）

- **主張**：_build_live_agg 以「latest − 90 個日曆天」取歷史，再用 tail(n−1).sum() 當作 MA 的部分和，並固定除以 n；算出的 _window_len 沒有任何地方用來檢查視窗是否足夠。只要窗內不足 59 個交易日（連假季），或個股上市未滿 n 根，MA60（以及 MA20、MA30）就會少加幾天卻仍除以 n，得到非 null 的錯值。這違反 CONTRIBUTING 3.3「窗口…按實際交易日，不得用自然日直接替代」。
- **正確的定義或數值**：MA 部分和取最近 n−1 個交易日；歷史不足 n−1 根時該 MA 為 null，與全量路徑 rolling_mean 的語意一致。
- **app 實際行為**：視窗以日曆天截斷，歷史不足時靜默算出偏低的 MA（最多低約 9%，新上市股可低到只有收盤價的 1/4）。
- **對使用者的影響**：即時行情開啟時，K 線最後一根活蠟燭（A5 的注入路徑）與盤中快照的 MA60 每年 2–5 月會明顯下凹，盤中的 MA20／60 金叉、站上均線類條件（策略與監控）可能誤觸發。收盤後由 refresh_recent 重算才恢復正確。目前 0 條監控規則，影響以圖表與盤中策略為主。
- **修法建議**：改成以交易日數取歷史（例如先取每檔最後 60 列），並在 _window_len < n−1 時讓對應的 MA、極值與動量為 null；同時把 90 天常數改成依交易日回推。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)  # 日历90天 ≈ 60个交易日
  - backend/app/repository.py:904-908 pl.col("close").tail(4)…tail(59).sum().alias("_ma60_partial_sum")
  - backend/app/repository.py:930 _window_len 只被計算，搜尋全 repo 除了清理欄位外沒有其他使用
  - backend/app/indicators/pipeline.py:1515-1520 ((_ma60_partial_sum + close) / 60)
  - CONTRIBUTING.md:105
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential （SMA＝特定期數的平均，期數以交易日計）
- **本地計算證據**：
  - a02_incr.out [B]：以店內真實交易日曆統計，599 個基準日中有 137 個的 90 個日曆天視窗不足 59 個交易日（最少 54 天），集中在每年 2–5 月（2024-04/05、2025-02～04、2026-02～05）。
  - a02_incr.out [C]：重現基準日 2024-05-02、目標日 2024-05-03，10 檔的 ma60 增量值比全量低 7.0%～8.8%（例如 2454 為 1005.33 vs 1084.22）；signal_ma_golden_20_60 在 00885、2454、3545 被誤判為 true（全量為 false）。
  - a02_newlist.py：2026-09-23 新上市的 00409A（前面只有 15 根）增量 ma20／ma30／ma60 為 7.809／5.206／2.603（收盤 10.14），全量為 null；7855 的 ma60_inc 為 22.88（收盤 42.0），全量為 null。
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_build_live_agg 以「基準日 − 90 個日曆天」切歷史，再用 tail(n−1).sum() 當 MA 部分和，並固定除以 n；_window_len 算出後沒有任何檢查。後果分兩種情況：

- 連假季：90 天內不足 59 個交易日（605 個基準日中有 143 個，最少 54 根，集中在每年 2–5 月）時，盤中增量的 MA60 會少加 1～5 天卻仍除以 60，比全量低約 6–9%（重現結果：2024-05-03 自選 9 檔低 7.0–8.8%；ma20＞ma60 金叉在 00885、2454、3545 誤判為 true）。MA20／MA30 在這個情況下不受影響。momentum_60d 需要 60 根，不足 60 根的 154 天都會取錯基準價；high_60d／low_60d 涵蓋的天數也不足 60。
- 新上市股：上市未滿 n 根時（例如 2026-09-02 上市的 00409A、2026-08-11 起的 7855），增量的 MA20／30／60、momentum_60d、high/low_60d 會算出非 null 的錯值，全量路徑則為 null。

這違反 CONTRIBUTING 3.3（按實際交易日，不得用自然日替代）。錯值只存在於盤中記憶體快取，會出現在 K 線活蠟燭與盤中快照／訊號中，不落盤，隔日重建後恢復。以使用者情境來說：目前（2026-09）自選股的視窗有 59 根，不會發生；下一次預計從 2027 年春節後開始。新上市股的錯值只在打開其 K 線時經由 fetch_live_bar 出現。嚴重度維持 P2。
    - 理由：我逐行讀過現行程式碼（repository.py 與 pipeline.py 相對 HEAD 都沒有修改），並用 repo 內的 KlineRepository._build_live_agg 搭配 compute_enriched_today 獨立重現，finding 的核心主張成立。

一、程式碼行為。
- live_agg 從歷史快取取資料時用的條件是 date >= latest − 90 個日曆天（repository.py:746、761-763）。
- MA 的部分和取 tail(n−1).sum()（904-908），pipeline.py:1516-1520 一律除以 n，沒有檢查視窗長度。
- _window_len 在 repository.py:930 算出後，只在 pipeline.py:1703 的清理清單被丟掉，另外只出現在測試 fixture，沒有任何邏輯用它判斷視窗是否足夠。
- 全量路徑用 rolling_mean(60)（pipeline.py:401）。polars 的 min_samples 預設等於 window_size，所以歷史不足時結果是 null。兩條路徑的語意因此不一致。

二、定義依據。以下三個來源都與 finding 引用的 StockCharts 不同：
- Wikipedia：SMA 是「the unweighted mean of the previous k data-points」。
- CMoney 投資小學堂：60 日均線是過去 60 天收盤價的平均；5 日線又稱週線，因為台股每週交易 5 天，可見以交易日計。
- 專案 CONTRIBUTING.md:105 明文規定窗口按實際交易日計算，不得用自然日直接替代。

MA60 應該是最近 60 根交易日 K 線的收盤價平均。用 90 個日曆天截斷，在連假季會少算幾根，卻仍除以 60，是實作錯誤。

三、本地重現。
- 交易日曆：以 kline_daily_enriched 的 661 個分區當交易日曆（休市日與台灣國定假日相符），605 個可評估基準日中有 143 個的 90 天視窗不足 59 根，最少 54 根，集中在 2024-04～05、2025-02～04、2026-02～05。finding 寫 137/599，數字小有差異，但結論相同。
- 自選股、基準日 2024-05-02：自選 9 檔的 ma60 增量值比全量低 7.0%～8.8%，例如 2454 為 1005.33 對 1084.22。signal_ma_golden_20_60 在 00885、2454、3545 被誤判為 true，與 finding 完全一致。
- 基準日 2026-04-30：低 6.2%～9.4%。
- 現況：基準日 2026-09-22 的視窗有 59 根，兩條路徑一致，所以目前自選股不受影響。
- 新上市股：00409A（5 月以來第一根 K 線 2026-09-02，基準日前 15 根）的增量 ma20／30／60 為 7.809／5.206／2.603，全量為 null；7855 的 ma60 增量為 22.88，全量為 null。也與 finding 一致。

四、使用者情境。
- 盤中輪詢走 _flush_live_enriched(asset_type="stock") 的增量路徑（quote_service.py:883、1009、1708-1716）。純台股部署下 ETF 也在股票表，實測 0050 等 4 檔 ETF 都在 kline_daily_enriched。
- K 線活蠟燭直接複製增量列的 ma5～ma60（api/kline.py:577-584），池外標的經由 fetch_live_bar 取得（kline.py:547）。
- 寫盤只存 ENRICHED_STORAGE_COLS，不含 MA（pipeline.py:92-100）。錯值只留在盤中記憶體，隔日重建後恢復。

因此不構成 P0 等級的資料破壞。錯的是盤中顯示值與訊號，而且每年約 24% 的交易日會發生，P2（明確功能錯誤）恰當。

五、finding 的小瑕疵（不影響成立）。
- 00409A 並非 2026-09-23 才新上市，資料中第一根是 2026-09-02。
- 連假季只影響 MA60，不影響 MA20／MA30（最短視窗仍有 54 根）。MA20／MA30 只在新上市股受影響。
- 漏掉幾個受同一原因影響的欄位：momentum_60d 用 tail(60).first()，需要 60 根，605 天中有 154 天不足，即使剛好 59 根也會差一天；high_60d／low_60d 也只涵蓋較少天數。

範圍外觀察：增量的 high_60d 取「最高價」，全量取收盤價的 rolling_max（pipeline.py:438），兩者口徑不同，實測 2454 為 5480 對 5185。這與本 finding 無關，我沒有進一步驗證。
    - 證據：
      - backend/app/repository.py:746 `start_60d = latest - timedelta(days=90)  # 日历90天 ≈ 60个交易日`；:761-763 以 date >= start_60d 切歷史快取（快取本身有 300 天，見 :570）
      - backend/app/repository.py:904-908 tail(4/9/19/29/59).sum() 當 MA 部分和；:920 `_close_60d_ago` = tail(60).first()；:930 `_window_len` = tail(59).len()
      - backend/app/indicators/pipeline.py:1516-1520 `((_maN_partial_sum + close) / N)` 固定除以 N；:1703 `_window_len` 只出現在清理清單。grep 全 app 只有這兩處，另有 tests\test_realtime_enriched_resume.py:134 的 fixture
      - backend/app/indicators/pipeline.py:401 全量 `rolling_mean(60)`；polars 文件 https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.rolling_mean.html：「If set to None (default), it will be set equal to window_size」，不足時回傳 null
      - CONTRIBUTING.md:105「窗口、前 N 日和批次回算均按實際交易日，不得用自然日直接替代。」
      - https://en.wikipedia.org/wiki/Moving_average：「In financial applications a simple moving average (SMA) is the unweighted mean of the previous k data-points.」
      - https://www.cmoney.tw/learn/course/technicals/topic/485：「60 日均線（季線、60 MA）是過去 60 天收盤價的平均值」；「因為台股每週交易 5 天，故又稱為週線」（以交易日計）
      - 本地 cal.py／cal60.py（work\v_a02_livewin_def）：661 個交易日分區、605 個可評估基準日；不足 59 根 143 個（最少 54 根），不足 60 根 154 個；月份分布 2024-04:20、2024-05:7、2025-02:19、2025-03:21、2025-04:18、2026-02:4、2026-03:22、2026-04:20、2026-05:12
      - 本地 repro.py（呼叫 KlineRepository._build_live_agg＋compute_enriched_today 與 compute_indicators 比對）基準日 2024-05-02→2024-05-03：_window_len=54；ma60 誤差 −7.03%（2379）～−8.85%（3545），2454 為 1005.33 對 1084.22；g2060 增量值為 true、全量為 false 的有 00885、2454、3545；ma20 誤差 0
      - 本地 repro.py 基準日 2026-04-30→2026-05-04：ma60 誤差 −6.2%～−9.4%（3545 的 _window_len=54）；基準日 2026-09-22→09-23：_window_len=59，9 檔 ma60 誤差 0（目前不發生）
      - 本地 repro_new.py 基準日 2026-09-22→09-23：00409A（第一根 2026-09-02，_window_len=15）ma20／ma30／ma60 增量為 7.809／5.206／2.603，全量為 null；momentum_60d 增量 0.045 對全量 null；7855 ma60 增量 22.88 對全量 null；009829 ma30 增量 8.494 對全量 null
      - backend/app/services/quote_service.py:1701-1716 股票走增量路徑；:883／:1009 輪詢呼叫；backend/app/api/kline.py:547 池外標的呼叫 fetch_live_bar，:577-584 把 ma5～ma60 注入活蠟燭
      - backend/app/indicators/pipeline.py:92-100 ENRICHED_STORAGE_COLS 不含 MA，錯值不落盤，只影響盤中記憶體快取
      - docs/code-review-2026-09-22.md 沒有記錄此問題（搜尋 live_agg、90天、自然日、_window_len 均無對應項）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：_build_live_agg（repository.py:746）用「基準日 − 90 個日曆天」截取歷史，再以 tail(n−1).sum() 當 MA 部分和，並在 pipeline.py:1516-1520 固定除以 n；_window_len（repository.py:930）算出來後沒有被使用。只要窗內列數少於 n−1，增量 MA 就會少加幾天卻仍除以 n，得到非 null 的偏低值；全量路徑 rolling_mean 在同樣情況回傳 null，違反 CONTRIBUTING.md:107。

觸發有三種情況：
(a) 連假季：以本地和 Yahoo ^TWII 日曆，2024-04～2026-09 共 605–606 個基準日中有 143 個的 90 天視窗只有 54–58 個交易日，集中在每年 2–5 月。自選股 MA60 因此低估約 1.3%–9.5%，視窗越短越嚴重：58 天約 −1.7%，55 天約 −6.4%，54 天約 −8%。例如 2024-05-03 的自選 10 檔低 7.0%–8.9%（2454 為 1005.33 vs 1084.22），全市場有 862 檔 MA20／60 金叉誤判，其中自選股是 00885、2454、3545。
(b) 個股缺交易日（停牌、分割等），全年都可能發生：0050 在 2025-06～07 最多低 5.9%，3545 在 2026-02～05 最多低 11.2%；正常季節的 2026-09-23 全市場仍有 14 檔 ma60 應為 null 卻有數值，5 檔金叉誤判，最差低 33.5%。
(c) 上市未滿 n 根：00409A（2026-09-02 上市）和 7855（2026-08-11 上市）的 MA20／30／60 算出錯值，例如 00409A 的 MA60 只有收盤價的約 1/4，全量則為 null。

MA20／MA30 只受 (b)、(c) 影響，連假季的 90 天視窗至少還有 54 天，足夠它們使用。

錯值只存在記憶體的盤中 enriched 快取，會進入 K 線活蠟燭，也包括非自選股按需開圖時的 fetch_live_bar，以及盤中條件判斷，不會落盤。金叉誤判主要發生在當日第一輪增量計算，因為之後的 prev 會換成當日值；MA60 偏低與「站上 MA60」類判斷則整天持續。
    - 理由：核心缺陷完全重現：我用真實的 KlineRepository._build_live_agg（傳入假的 self，只改記憶體）和 compute_enriched_today 重算，finding 舉的每個樣本數字都一字不差。視窗不足時，MA 仍固定除以 n，得到非 null 的偏低值；全量路徑 rolling_mean(60) 在資料不足時會回傳 null，兩條路徑語意不一致。_window_len 除了清理欄位之外沒有任何地方使用。

判為部分成立，是因為以下細節與量化有誤：
(1) 基準日數：我用店內日曆算出 605 個基準日，其中 143 個不足 59 個交易日；改用 Yahoo ^TWII 日曆算出 606 個，也是 143 個不足。finding 寫 599／137，分母取法不同，但最少 54 天、集中在 2–5 月這兩點相符。
(2) 標題的「2–5 月 MA60 低估 7–9%」只適用於最差的 54–55 日視窗。以自選 9 檔加 2330 在全部不足基準日實算，誤差範圍約 −1.3% 到 −9.5%，各月中位數從 −3.3%（2025-02）到 −7.6%（2026-05）。另外 3545 在 2026-05 因個股缺交易日，最多低 −11.2%。
(3) 00409A 並非 2026-09-23 上市：官方 ETF 基本資料的 listing_date 是 2026-09-02，所以到基準日 2026-09-22 只有 15 根。7855 的官方上市日是 2026-08-11。
(4) CONTRIBUTING 的「不得用自然日直接替代」在第 107 行，不是第 105 行。
(5) 新面向：除了連假季，個股缺交易日也會觸發同一問題，而且全年都可能發生。例如 0050 在 2025-06～07 分割停牌期間最多低 −5.9%，3545 在 2026-02～05 最多低 −11.2%。在正常季節的 2026-09-23，全市場仍有 14 檔 ma60 應為 null 卻算出數值，並有 5 檔金叉誤判（1213、3591、5906、9110、911622），最差低 −33.5%。

影響面的補充（從程式碼推斷，未在實際盤中執行）：第一次 merge 之後，_enriched_cache 會換成當日列（repository.py:2303），之後的輪詢以當日前一輪的值當 prev，所以 MA20／60 金叉誤判主要只出現在當日第一輪輪詢。MA60 值本身偏低則整天持續，會影響 K 線活蠟燭與「站上 MA60」類條件。對非自選股，開啟 K 線時會走 fetch_live_bar，也會進入同一條增量路徑，因此新上市股確實可能看到錯值。錯值不會寫進磁碟，因為 ENRICHED_STORAGE_COLS 只含基礎欄位。嚴重度維持 P2。
    - 證據：
      - 程式碼：backend/app/repository.py:746 start_60d = latest - timedelta(days=90)；:904-908 用 tail(4/9/19/29/59).sum() 算部分和；:930 _window_len 只被計算。grep 全 app 目錄，_window_len 只出現在這裡和 pipeline.py:1703 的 drop_cols
      - 程式碼：backend/app/indicators/pipeline.py:1516-1520 固定除以 5/10/20/30/60，沒有依視窗長度做 null 防護；全量路徑 pipeline.py:401 用 rolling_mean(60)，資料不足時為 null（本地資料證實 00409A 全量 ma20/30/60 = null）
      - CONTRIBUTING.md:107（不是 105）：「窗口、前 N 日和批次回算均按實際交易日，不得用自然日直接替代。」
      - 本地重算 verify.py（AUDIT_SCRATCH/work/verify-liveagg90/verify.out）Part A：店內日曆 605 個基準日，143 個不足 59 天（finding 為 599/137），最少 54 天。各天數的基準日數為 54:11、55:68、56:34、57:27、58:3；月份為 2024-04/05、2025-02~04、2026-02~05
      - 重現基準日 2024-05-02、目標日 2024-05-03（視窗 [2024-02-02, 2024-05-02] 只有 54 個交易日，_window_len=54），ma60 增量值 vs 全量值：2454 為 1005.333333 vs 1084.216667（−7.28%），與 finding 的 1005.33 vs 1084.22 相同；3545 為 85.085 vs 93.343（−8.85%）；2379 為 506.25 vs 544.525（−7.03%）。10 檔誤差範圍 −7.03%～−8.85%，finding 寫 7.0%～8.8%，相符。ma20／ma30 與全量完全相同
      - 同一案例 signal_ma_golden_20_60 增量為 true、全量為 false 的有 00885、2454、3545，與 finding 相同。全市場 2042 檔中有 862 檔金叉誤判，沒有反向誤判；全市場 ma60 誤差中位數 −8.14%，範圍 −4.3%～−24.6%
      - 新上市股（基準日 2026-09-22、目標日 2026-09-23）：00409A 有 15 根，增量 ma20/30/60 = 7.809/5.206/2.603，全量為 null；7855 有 31 根，ma60_inc = 22.880833，全量為 null。兩者都與 finding 相同。同日自選 10 檔 _window_len=59，誤差 0，作為對照組
      - 官方參考資料 csv/twse_t187ap47_L_etf_meta.csv：00409A 的 listing_date 是 2026-09-02；csv/twse_t187ap03_L_share_capital.csv：7855 的 listing_date 是 2026-08-11；csv/twse_mi_index_20260923_quotes.csv：00409A 在 9/23 收盤 10.14、7855 收盤 42.0，與本地資料一致
      - 官方 STOCK_DAY（2026-08～09-23 共 380 列）與本地 raw_close 逐筆比對：缺漏 0、不符 0；交易日 38 天，集合完全相同。以官方收盤算出的 9/23 MA20（例如 2454 為 4531.25、0050 為 108.4125）與本地全量值、增量值相同
      - 獨立日曆來源 Yahoo https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?period1=1701388800&period2=1790208000&interval=1d：本地跨度內有 662 個交易日，比本地多 2026-07-10 一天。用 Yahoo 日曆重算：606 個基準日中 143 個不足，最少 54，結論不變
      - verify2.py 對自選 10 檔、602 個基準日向量化重算 ma60（輸出 verify2.out）：視窗天數 k=58 時中位數 −1.73%，k=57 為 −3.27%，k=56 為 −4.93%，k=55 為 −6.39%，k=54 為 −7.98%（範圍 −6.8%～−9.5%），k=53 為 −10.8%（範圍到 −11.2%）。不足基準日的誤差整體範圍是 −1.32%～−11.18%
      - verify3.py：個股缺交易日造成的額外不足包括 0050（2025-06-17～2025-07-30，32 列，最多 −5.94%）與 3545（2026-02-26～2026-05-20，56 列，最多 −11.18%）。正常季節 2026-09-23 全市場有 14 檔 ma60 應為 null 卻有數值，5 檔金叉誤判（1213、3591、5906、9110、911622），最差 −33.5%
      - 線上定義 https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential：「a 5-day simple moving average is the five-day sum of closing prices divided by five.」
      - 影響面：app/api/kline.py:559-584 把 q 的 ma5～ma60 寫進活蠟燭；quote_service.py:806-810 的 fetch_live_bar 對非自選股也走增量路徑；repository.py:2303 在第一次 merge 後把 _enriched_cache 換成當日列，所以之後輪詢的 prev 是當日值（推斷：金叉誤判主要出現在第一輪）；pipeline.py:92-101 ENRICHED_STORAGE_COLS 只含基礎欄位，錯值不落盤
      - 唯讀確認：前後 git status 相同，只有原本就有的 3 個修改檔；所有產出都在 scratchpad\audit\work\verify-liveagg90\

### [P2 → P3] MACD 柱狀體用中國慣例 2×(DIF−DEA)，並標成「MACD」，與台灣慣例（DIF／MACD／OSC）不符（部分成立）

- **主張**：後端 macd_hist＝(DIF−DEA)×2（中國通達信慣例），前端副圖把它標成「MACD」，把訊號線標成「DEA」。台灣慣例是：訊號線叫 MACD（DIF 的 9 日 EMA），柱狀體叫 OSC＝DIF−MACD，不乘 2；Appel／StockCharts 的 Histogram 也不乘 2。純台股部署的使用者拿去和台灣看盤軟體比，「MACD」數值會對不上：app 顯示的是 2×OSC，而台灣的「MACD」指的是訊號線。
- **正確的定義或數值**：台股部署採台灣慣例：線名 DIF／MACD(訊號)／OSC，柱＝DIF−MACD；或至少把柱標成「MACD柱 (×2)」並在說明註明口徑。
- **app 實際行為**：柱值是 2 倍，而且叫「MACD」；訊號線叫 DEA。
- **對使用者的影響**：使用者對照 Yahoo 奇摩、券商 App 時，柱狀體數值差一倍，「MACD」指的也是不同的線，容易誤判 MACD 值或把 app 的數值當成錯的。
- **修法建議**：由使用者裁決口徑。建議台股改為 OSC＝DIF−DEA（不乘 2），前端標為 DIF／MACD／OSC；若保留 ×2，至少把標籤改為「MACD柱(×2)」，並在 ENRICHED_COLUMNS 說明與欄位 tooltip 註明。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:147 "macd_hist": "MACD柱状图 (DIF-DEA)×2"
  - backend/app/indicators/pipeline.py:458-461 ((macd_dif - macd_dea) * 2)
  - backend/app/indicators/pipeline.py:1511 ((dif - dea) * 2)
  - backend/app/taiwan/technical_indicators.py:117 (_macd_dif_raw - _macd_dea_raw) * 2.0
  - frontend/src/components/EChartsCandlestick.tsx:201-228（series 名稱 'DEA'、'MACD'；資訊列 label 'MACD' 顯示 macd_hist）
  - frontend/src/lib/watchlist-columns.ts:51-53（'MACD-DEA'、'MACD柱'）
- **線上證據**：
  - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=c1c097ed-e22a-477e-bf84-ddc0915c40e2 ：「將DIF線取九天EMA平均值即得（MACD線）」、「柱線 = DIF線–MACD線」
  - https://zh.wikipedia.org/zh-tw/%E6%8C%87%E6%95%B0%E5%B9%B3%E6%BB%91%E7%A7%BB%E5%8A%A8%E5%B9%B3%E5%9D%87%E7%BA%BF ：「OSC=DIF−DEM=DIF−MACD」
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator ："MACD Histogram: MACD Line - Signal Line"
  - https://xueqiu.com/4416664518/260895363 （搜尋結果標題：通達信預設寫法 DIF:EMA(CLOSE,SHORT)-EMA(CLOSE,LONG);DEA:EMA(DIF,MID);MACD:(DIF-DEA)*2，屬中國慣例）
  - WebSearch 摘要（來源頁不明，plausible）：2454「MACD9 −77.74、DIF −126.97、OSC −49.24」，即 OSC＝DIF−MACD9、不乘 2
- **本地計算證據**：
  - a02_main.out [4]：2454 在 2026-09-23，app 副圖「MACD」顯示 113.229（＝2×56.614）；按台灣慣例，MACD（訊號線）為 206.135，OSC 為 56.614。0050：app 0.676，台灣 OSC 0.338。
  - 數值本身與獨立重算的 2×(DIF−DEA) 一致（最大差 2.0e-12），問題在口徑與標示，不在計算。交叉訊號只看正負號，不受倍數影響。
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：後端所有 MACD 實作（pipeline.py、taiwan/technical_indicators.py、backtest/matrix.py）的柱狀體都是 (DIF−DEA)×2，前端副圖把訊號線標成「DEA」、柱狀體標成「MACD」。這是中國軟體的命名與倍數，而且程式內部一致、計算正確；金叉與死叉只看兩線相對位置，不受影響。台灣常見的慣例是 DIF／MACD（訊號線）／OSC＝DIF−MACD，柱狀體不乘 2（來源：股感、ezchart、XQ 命名；富途明說 ×2 只是部分軟體的放大顯示）。純台股使用者拿 app 和台灣看盤軟體對照時，「MACD」標籤指的是另一條線（例如 2454.TWSE 最新一筆：app 的「MACD」＝113.229，台灣慣例的 MACD 訊號線＝206.135，OSC＝56.614），容易誤讀。這屬於標示與在地化的局部體驗問題（P3），不是計算錯誤或功能錯誤。是否改口徑依 CONTRIBUTING 須由使用者裁決。另外，XQ 預設用加權收盤價計算（plausible），就算改了命名與倍數，數值也不保證與所有台灣軟體一致。
    - 理由：事實面全部成立：後端三處 MACD 實作都把柱狀體算成 (DIF−DEA)×2，前端副圖把訊號線標成「DEA」、柱狀體標成「MACD」。這是中國軟體的命名與倍數。台灣常見的定義則是 DIF／MACD（DIF 的 9 日 EMA，也就是訊號線）／OSC＝DIF−MACD，柱狀體不乘 2。我另外找了三個與 finding 不同的來源：股感、ezchart 與富途，都支持「基本柱狀體＝兩線相減」；富途還明說「×2」只是部分軟體為了顯示放大的做法。本地 parquet 實測 2454.TWSE 最新一筆：macd_dea=206.135、macd_hist=113.229，DIF−DEA=56.614，比值剛好 2.0，與 finding 的數值一致。

嚴重度我認為高估了。×2 是有來源支持的替代慣例，程式也在 pipeline.py:147 明寫「(DIF-DEA)×2」，而且全 repo 一致，計算本身沒有錯。金叉、死叉只看 DIF 與 DEA 的相對位置，不受影響。回測因子另有 macd_hist_pct 可用。使用者目前 0 條監控規則，也沒有依賴柱狀體絕對值的門檻。實際影響只有台灣使用者對照看盤軟體時，「MACD」這個標籤指的是另一條線、數值差一倍，屬於標示與在地化的體驗問題，較接近 CONTRIBUTING.md 第 11 節的 P3（局部體驗），不是 P2 的明確功能錯誤。

補充一個 finding 沒提到的面向（依據是 WebFetch 的摘要，未取得原文，屬 plausible）：XQ 的 MACD 預設輸入是加權收盤價，而不是收盤價。所以即使改成台灣的命名與倍數，拿去和 XQ 比，DIF 與 MACD 仍可能對不上，修正時的說明文字不宜承諾「會與看盤軟體一致」。
    - 證據：
      - backend/app/indicators/pipeline.py:146-147 "macd_dea": "MACD DEA线(信号线)"、"macd_hist": "MACD柱状图 (DIF-DEA)×2"
      - backend/app/indicators/pipeline.py:458-461 ((pl.col("macd_dif") - pl.col("macd_dea")) * 2).alias("macd_hist")
      - backend/app/indicators/pipeline.py:1507-1511 遞推版 ((dif - dea) * 2).alias("macd_hist")
      - backend/app/taiwan/technical_indicators.py:28（docstring「(dif - dea) * 2 = hist」）、116-118 (_macd_dif_raw - _macd_dea_raw) * 2.0
      - backend/app/backtest/matrix.py:3842-3848 回測矩陣同樣是 (dif - dea) * 2（finding 未列，但口徑一致）
      - frontend/src/components/EChartsCandlestick.tsx:201 name: 'DEA'；:209 name: 'MACD'（bar，資料來源 macd_hist）；:227-228 資訊列 label 'DEA'、'MACD' 顯示 macd_hist.toFixed(3)
      - frontend/src/lib/watchlist-columns.ts:51-53 標籤 'MACD-DIF'、'MACD-DEA'、'MACD柱'；frontend\src\lib\signals.ts:193 與 backend\app\strategy\monitor.py:58 使用同樣的標籤
      - backend/app/indicators/pipeline.py:631-636 金叉／死叉只比較 macd_dif 與 macd_dea，不受 ×2 影響
      - 本地計算：cd backend && uv run python scratchpad\audit\work\verify_macd_def\check.py，讀 data\kline_daily_enriched 最後兩個分區的 2454.TWSE → macd_dif=262.749、macd_dea=206.135、macd_hist=113.229、dif-dea=56.614、ratio=2.0；前一筆 macd_hist=95.256、dif-dea=47.628、ratio=2.0
      - https://www.stockfeel.com.tw/macd-dif-eam/ （股感，台灣）：DIF＝EMA(12)−EMA(26)；MACD（慢線／訊號線）＝EMA(DIF,9)；「OSC = DIF – MACD」，沒有乘 2
      - https://www.ezchart.com.tw/inds.php?IND=MACD ：「DIF=nEMA－mEMA」、「xMACD=(前一日xMACD*(x-1)＋DIF×2)/(x+1)」（此處 ×2 是 EMA 平滑係數）、「一般會將DIF-MACD繪柱形圖(BAR)」
      - https://www.futuhk.com/blog/detail-macd-9-250663005 （富途）：「柱狀圖（MACD直方圖）= MACD線-訊號線」；另註「為了更直觀地展示柱狀圖，會將其數值放大一倍，即柱狀圖=（MACD線-訊號線）×2」，可見 ×2 是部分軟體的顯示慣例，不是標準定義
      - https://xshelp.xq.com.tw/XSHelp/?HelpName=MACD&group=TECHINDEXFUNC （XQ 全球贏家，台灣）：函式簽名「MACD(數列,短期數,長期數,MACD平滑期數,輸出DIF值,輸出MACD值,輸出OSC值)」，確認台灣軟體命名為 DIF／MACD／OSC；本頁未寫 OSC 公式；摘要指出預設數列為 WeightedClose（plausible）
      - https://tw.stock.yahoo.com/quote/2454.TW/technical-analysis ：頁面由前端動態產生，抓不到 MACD 數值，無法用 Yahoo 奇摩直接對照
      - CONTRIBUTING.md:294-297 嚴重度定義：P2＝明確功能錯誤；P3＝維護性、局部體驗、文檔
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：數值完全重現：後端所有 MACD 計算路徑都把 macd_hist 算成 2×(DIF−DEA)（通達信慣例），9 檔自選的 5934 列比值全部是 2.0。前端 K 線副圖把這個 2 倍值的柱狀體標成「MACD」、訊號線標成「DEA」，自選表欄位標成「MACD柱」，都沒有註明 ×2。以 2454 在 2026-09-23 為例：DIF 262.749、訊號線（台灣稱 MACD9）206.135、台灣 OSC 56.614，而 app 顯示「MACD」113.229。台灣網站 poorstock 同日顯示 Hist 52.99（＝Diff−Sig），沒有乘 2。0050 的台灣 OSC 是 0.338，app 顯示 0.676。計算本身沒有算錯，交叉訊號和 macd_hist_pct 排序也不受影響，問題在台股部署的標示和口徑與台灣慣例（DIF／MACD／OSC，柱＝DIF−MACD）不同。後端 ENRICHED_COLUMNS 已經註明「(DIF-DEA)×2」，並透過規則與訊號 API 顯示在編輯器，所以要補的是 K 線副圖與自選表的標籤，不是 ENRICHED_COLUMNS。finding 引用的「DIF −126.97／MACD9 −77.74／OSC −49.24」日期不明，只能證明公式，不能拿來對照數值。嚴重度較接近 P3（局部體驗、口徑標示）；如果把台股慣例的標示當成功能規格，也可以列 P2。
    - 理由：我自己寫了腳本重算，finding 的數字全部重現。

1) 輸入資料正確：本地 taiwan/daily 的收盤價和官方 TWSE STOCK_DAY 參考資料逐日比對，自選 9 檔在 2026-08-01～09-23 共 38 個交易日，最大差 0.0。kline_daily_enriched 的 close 與 raw_close、taiwan/daily 的 close 最大差也都是 0.0。

2) 計算方式：我不用 polars 的 ewm，改用純 Python 迴圈重算 EMA12、EMA26、DIF 和 DEA（9 日）。在 2026-09-23 這天，app 的 macd_dif、macd_dea 和我算的差距在 1e-12 等級，macd_hist 則等於 2×(DIF−DEA)。
- 2454：DIF 262.749184，DEA（台灣稱 MACD9）206.134798，台灣 OSC（DIF−DEA）56.614385，app 顯示 113.228771，正好是 OSC 的 2 倍，finding 寫 113.229／206.135／56.614，一致。
- 0050：OSC 0.338227，app 0.676454，finding 寫 0.338／0.676，一致。
- 其餘 7 檔也全是 2 倍，例如 3034 是 −0.20518 對 −0.41036。
- 9 檔全部 enriched 列中，DIF−DEA 不為 0 的共 5934 列，hist÷(DIF−DEA) 的最小值與最大值都是 2.0。
- 把 app 的純函式 compute_indicators 套到 taiwan/daily 上，算出的比值同樣是 2.0。
- 我另外用 SMA 當 EMA 起始值再算一次，09-23 的 OSC 小數點後 6 位相同，所以起始值寫法不影響結論。

3) 標示：K 線副圖的柱狀體 series 名稱和資訊列都標成「MACD」，訊號線標成「DEA」（EChartsCandlestick.tsx:201-228）；自選表欄位標成「MACD柱」，沒有註明 ×2（watchlist-columns.ts:53）。

4) 線上定義：MoneyDJ 寫「柱線 = DIF線–MACD線」，MACD 線是「將DIF線取九天EMA平均值即得」；維基百科中文版寫「OSC = DIF − DEM = DIF − MACD」；StockCharts 寫「MACD Histogram: MACD Line - Signal Line」。三者都沒有乘 2。

5) 台灣網站的實際數值：poorstock.com（散戶鬥嘴鼓）2454 在 2026-09-23 顯示「Diff: 259.55 / Sig: 206.56 / Hist: 52.99」，259.55−206.56＝52.99，沒有乘 2。它的柱狀體數值接近 app 的 OSC 56.61，而 app 顯示的是 113.23。兩邊 DIF 差約 3.2，原因我沒查（推測是資料源或價格基準不同），但不影響「柱狀體不乘 2」這個結論。

需要補充修正的地方：
(a) finding 線上證據第 5 項「2454 MACD9 −77.74、DIF −126.97、OSC −49.24」是負值，不可能是 2026-09-23 的數值（app 那天的 DIF 是 +262.7），日期不明。它只能證明 OSC＝DIF−MACD9 這個公式，不能拿來和 app 的數值對照。
(b) 修正建議說要「在 ENRICHED_COLUMNS 說明註明口徑」，但 pipeline.py:147 早就寫成「MACD柱状图 (DIF-DEA)×2」，而且這段說明已經由 monitor_rules 與 signals API 當作欄位標籤輸出。真正沒註明 ×2 的地方是 K 線副圖和自選表欄位。
(c) 嚴重度：計算本身符合它自己註明的中國通達信慣例，沒有算錯。金叉、死叉只看正負號，不受影響。macd_hist_pct 乘上常數 2 不改變排序。自訂門檻也是用 app 自己的單位。所以影響是顯示標籤、以及和台灣看盤軟體對照時的口徑落差，比較符合 CONTRIBUTING.md 第 11 節的 P3「局部體驗」。如果把台股慣例的標示當成功能規格，P2 也說得通，所以列為 P3（可議）。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:147 "macd_hist": "MACD柱状图 (DIF-DEA)×2"；:458-461 ((macd_dif - macd_dea) * 2)；:1511 ((dif - dea) * 2)
      - 程式碼：backend/app/taiwan/technical_indicators.py:117 .then((_macd_dif_raw - _macd_dea_raw) * 2.0)
      - 程式碼：frontend/src/components/EChartsCandlestick.tsx:201（series 名稱 'DEA'）、:210（柱狀 series 名稱 'MACD'）、:228（資訊列 label 'MACD' 顯示 macd_hist.toFixed(3)）；frontend/src/lib/watchlist-columns.ts:52-53（'MACD-DEA'、'MACD柱'，沒有註明 ×2）
      - 程式碼：backend/app/api/signals.py:68 與 backend/app/api/monitor_rules.py:150 用 ENRICHED_COLUMNS 當欄位標籤，所以規則與自訂訊號編輯器已經顯示「(DIF-DEA)×2」
      - 本地計算指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-macdosc/repro.py（輸出存在同目錄的 repro.out，exit=0）
      - 本地輸入核對：taiwan/daily 收盤價對照官方 twse_stock_day_202608_202609.csv，自選 9 檔各 38 天，max_abs_diff 全部 0.0；enriched close 對 raw_close、對 taiwan/daily close 的差也都是 0.0（表中 2330 缺值是因為腳本只篩選 9 檔自選，不是資料問題）
      - 並列比較 2454 @2026-09-23：finding 寫 app「MACD」113.229、MACD9 206.135、OSC 56.614；我重算 DIF 262.749184、DEA 206.134798、OSC 56.614385、2×OSC 113.228771；app 存值 hist 113.228771，|app_hist−2×OSC|=9.7e-13
      - 並列比較 0050 @2026-09-23：finding 寫 app 0.676、台灣 OSC 0.338；我重算 OSC 0.338227、2×OSC 0.676454；app 存值 0.676454
      - 其餘自選 @2026-09-23（台灣 OSC → app hist）：3034 −0.20518→−0.41036；2379 6.139623→12.279247；6196 3.467297→6.934595；3545 0.241527→0.483054；00878 0.085444→0.170888；00885 −0.036138→−0.072275；00893 0.439444→0.878887
      - 全量比值：9 檔 enriched 共 5934 列（|DIF−DEA|>1e-9），hist÷(DIF−DEA) 的 min=2.0、max=2.0；app 純函式 compute_indicators 對 2454 與 0050 的 ratio_hist_over_osc=2.0
      - SMA 起始值版本在 2026-09-23 的 OSC 與 adjust=False 版本小數點後 6 位相同（2454 都是 56.614385），起始值寫法不影響結論
      - 線上：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=c1c097ed-e22a-477e-bf84-ddc0915c40e2 原文「DIF = 12EMA – 26EMA」、MACD 線「將DIF線取九天EMA平均值即得」、「柱線 = DIF線–MACD線」，沒有乘 2
      - 線上：https://zh.wikipedia.org/zh-tw/%E6%8C%87%E6%95%B0%E5%B9%B3%E6%BB%91%E7%A7%BB%E5%8A%A8%E5%B9%B3%E5%9D%87%E7%BA%BF 原文「OSC = DIF − DEM = DIF − MACD」，沒有乘 2
      - 線上：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator 原文「MACD Histogram: MACD Line - Signal Line」
      - 線上：https://poorstock.com/stock/2454 顯示 2026-09-23 17:00「Diff: 259.55 / Sig: 206.56 / Hist: 52.99」（259.55−206.56=52.99，沒有乘 2；接近 app 的 OSC 56.61，而 app 顯示 113.23）
      - WebSearch 摘要（來源頁不明）：2454「MACD 238.1373 / Signal 193.3318 / Histogram 44.8055」（238.1373−193.3318=44.8055，沒有乘 2；日期推測是 2026-09-22，app 當天 DIF 239.609、DEA 191.981、OSC 47.628、hist 95.256）
      - Yahoo 奇摩技術分析頁 https://tw.stock.yahoo.com/quote/2454.TW/technical-analysis 的 MACD 由前端動態產生，WebFetch 抓不到數值

### [P3 → P3] 全量 EMA／MACD 沒有暖機門檻，與自選頁路徑的 35 根門檻不一致（部分成立）

- **主張**：compute_indicators 的 ewm_mean 預設 min_samples=1，新上市第 1 根就輸出 EMA60 與 MACD（EMA 的初值就是首根收盤），前幾十根的值與交叉訊號幾乎只是暖機產物；technical_indicators.py 卻在不足 35 根時回傳 null。同一欄位在兩條路徑的空值語意不同。
- **正確的定義或數值**：兩條路徑的暖機規則一致，例如 MACD 在第 34 根（26+9−1）之前為 null，EMA_n 在第 n 根之前為 null。
- **app 實際行為**：圖表與 enriched 從第 1 根就顯示 MACD，自選頁則顯示「—」。
- **對使用者的影響**：新上市股與 ETF 的 MACD 副圖與早期交叉訊號不可信，而且與自選頁不一致；影響範圍小。
- **修法建議**：在 compute_indicators 對 ema*、macd_* 加 min_samples，或加上 bar 數門檻（與 MIN_BARS_MACD 共用常數）。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:402-415、454-461（ewm_mean 沒有 min_samples 或門檻）
  - backend/app/taiwan/technical_indicators.py:43 MIN_BARS_MACD = 35；:112-118 不足時為 None
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential （EMA 以前 n 期 SMA 起算，需要足夠歷史）
- **本地計算證據**：
  - a02_warm.py：2026-09-23 有 6 檔不足 35 根（00409A、009827、009828 各 16 根，009829 24 根，2237 33 根，7855 32 根），enriched 全部有非 null 的 macd_dif 與 ema60；歷史上（排除店內起始的 2024-01～02）各檔前 34 根內共觸發 1,106 次 MACD 金叉或死叉。
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：compute_indicators／compute_enriched 的 EMA 與 MACD 沒有暖機門檻（polars ewm_mean 預設 min_samples=1）。新上市第 1 根的 EMA 就等於收盤價，DIF 和 DEA 都是 0，之後每根都有數值。這是通達信式的首值起算慣例，不同於 TA-Lib 和 StockCharts 以 SMA 起算、MACD 在第 34 根前不輸出的做法。自選頁路徑 technical_indicators.py 在不足 35 根時回傳 null，所以同一欄位在兩條路徑的空值語意不一致。

2026-09-23 有 6 檔不足 35 根：00409A、009827、009828 各 16 根，009829 24 根，2237 33 根，7855 32 根。這 6 檔在 enriched 與日 K 圖上都有 ema60 與 MACD 數值，自選頁（MACD 欄位預設隱藏，要手動開啟）則為 null。

以首根日期晚於 2024-02-29 的 309 檔新上市標的計算，前 34 根共觸發 1,059 次 MACD 交叉（原稿的 1,106 是逐列篩日期得出的，混入了 27 次庫內起始標的與 20 次 2024 年 1～2 月首見標的的交叉）。其中 300 次落在第 2 根，是初值造成的必然產物，方向等於第 2 根漲跌。第 3～34 根的交叉率 7.81%，與成熟 K 棒的 8.06% 相當，所以交叉頻率沒有被暖機放大，但數值明顯偏向首根收盤（例如 EMA60 在第 16 根時，首根收盤仍佔約 61% 權重）。

使用者自選 9 檔都至少 150 根，監控規則 0 條，目前沒有實際影響。修正方向是統一兩條路徑的門檻；要注意 MIN_BARS_MACD=35 與 TA-Lib 的第 34 根差 1 根。
    - 理由：主張的核心已重現。compute_indicators／compute_enriched 的 EMA 與 MACD 沒有暖機門檻：polars 1.40.1 的 ewm_mean 預設 min_samples=1，新上市第 1 根 ema60 等於收盤價，DIF 和 DEA 都是 0，之後每根都輸出數值。technical_indicators.py 則在不足 35 根時回傳 null。2026-09-23 不足 35 根的 6 檔，代號與根數跟 finding 完全相同。這 6 檔在 enriched 上的 ema60 與 macd_dif 都不是 null；用同一段歷史餵 compute_taiwan_daily_indicators，全部是 null。自己用 compute_enriched 重算的結果與磁碟上的 enriched 完全一致（最大差 0.0）。

有三處需要修正。
(1) 「1,106 次」只有照原稽核腳本的「逐列篩 date>2024-02-29 且 bar_no<=34」才算得出來。這個篩法並沒有排除庫內起始的標的：27 次來自首根為 2024-01-02 的 2,017 檔既有標的（enriched 濾掉停牌列後，第 34 根落在 2/29 之後）；20 次來自首根在 2024-01-03 到 02-29 的 20 檔。改以「首根日期晚於 2024-02-29」逐檔篩選，是 309 檔、前 34 根共 1,059 次（金叉 529、死叉 530）。
(2) 「前幾十根的交叉訊號幾乎只是暖機產物」說得太重。真正由初值決定的只有第 2 根：309 檔中 300 檔在第 2 根必出交叉，方向等於第 2 根收盤相對第 1 根的漲跌；另 9 檔是平盤，沒有交叉。第 3 到 34 根共 759 次，交叉率 7.81%，與成熟 K 棒（第 60 根以後）的 8.06% 相當，頻率沒有被暖機放大。不過數值本身確實偏向初值：EMA60 在第 16 根時，首根收盤仍佔約 60.7% 權重。2237 與 7855 的 DIF 若改用 SMA 起算，分別是 −0.1216 和 −0.5243，pipeline 算出的是 −0.1063 和 −0.3732。
(3) 用首根收盤當 EMA 初值、首日 DIF=DEA=0，是通達信等軟體公開採用的慣例，不是算錯；TA-Lib 和 StockCharts 則用 SMA 起算，前面的 K 棒不輸出。所以缺陷的本質是「同一欄位在兩條路徑的空值語意不一致」，而不是某條路徑公式錯誤。另外，finding 的 expected 寫「第 34 根前為 null」，對應 TA-Lib 的 lookback 33；fix 建議卻要共用 MIN_BARS_MACD=35，那樣第一個有效值會落在第 35 根，兩者差 1 根。

影響方面，使用者自選的 9 檔在全庫與 150 日視窗內都 ≥150 根，監控規則 0 條，所以目前沒有實際影響。受影響的只有新上市標的的日 K 圖 MACD 副圖，以及讀 enriched 的選股與策略掃描（第 2 根必出交叉）。自選頁的 MACD 欄位預設隱藏，要使用者手動開啟才會看到「—」。維持 P3。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:403-416，ema5～ema60、_ema12、_ema26 用 ewm_mean(alpha=..., adjust=False)，沒有 min_samples；:446-461 的 macd_dif、macd_dea、macd_hist 也沒有門檻。已安裝的 polars 版本是 1.40.1，ewm_mean 的簽名是 (..., adjust=True, min_samples=1, ...)（指令：uv run python -c 'inspect.signature(pl.Expr.ewm_mean)'）
      - 程式碼：backend/app/taiwan/technical_indicators.py:43 MIN_BARS_MACD = 35；:112-118 在 _bar_count 不足 35 根時輸出 None。呼叫端 backend/app/taiwan/watchlist_enrichment.py:153-159 取最近 150 個交易日
      - 程式碼：圖表讀 enriched 的位置在 backend/app/api/kline.py:430；後備路徑 :435 也走 compute_enriched。前端 frontend/src/components/EChartsCandlestick.tsx:195 只要值不是 null 就畫；自選頁的 MACD 欄位在 frontend/src/lib/watchlist-columns.ts:51-53 預設 visible: false
      - 本地重現（腳本：scratchpad\audit\work\verify-emawarm\repro.py，輸出 out.txt）：2026-09-23 不足 35 根的標的，重現結果為 00409A 16、009827 16、009828 16、009829 24、2237 33、7855 32，與 finding 完全相同。這 6 檔的 enriched ema60 依序為 9.728358、9.992347、9.712024、10.689711、27.265628、43.05102，macd_dif 依序為 0.05338、0.087175、−0.072066、0.055699、−0.106277、−0.373217，都不是 null；compute_taiwan_daily_indicators 算出的 macd_dif、macd_dea、macd_hist、ma60 全部是 null
      - 本地重現：以 compute_enriched(daily, factors=空) 重算，2026-09-23 共 1,323 檔，與磁碟上 enriched 的 macd_dif 和 ema60 最大差都是 0.0。309 檔新上市標的（首根晚於 2024-02-29）的第 1 根，ema60 全部等於 close，macd_dif 與 macd_dea 全部等於 0，null 數為 0
      - 數字並列（腳本：crosses.py，輸出 crosses.out）：finding 的 1,106 只能用『逐列篩 date>2024-02-29 且 bar_no<=34』重現，重現值 1106。拆解如下：首根為 2024-01-02 的 2,017 檔貢獻 27 次；首根在 2024-01-03～02-29 的 20 檔貢獻 20 次；首根晚於 2024-02-29 的 309 檔貢獻 1,059 次（金叉 529、死叉 530）。同一口徑改算前 33 根得 1,033 次，前 35 根得 1,082 次
      - 本地：新上市標的第 2 根共 309 檔，其中上漲 117 檔全部觸發金叉、下跌 183 檔全部觸發死叉，平盤 9 檔沒有交叉。第 3～34 根共 759 次交叉，交叉率 0.0781；成熟 K 棒（bar_no>60）的交叉率是 0.0806
      - 本地：若改用 SMA 起算的 MACD，2237 的 DIF 是 −0.1216，pipeline 算出 −0.1063；7855 是 −0.5243，pipeline 算出 −0.3732。EMA60 在第 16 根時首根收盤的權重為 (59/61)^15 ≈ 0.607
      - 本地：自選 9 檔（2454、3034、2379、6196、3545、0050、00878、00885、00893）全庫為 656～661 根，150 日視窗內都是 150 根，目前不受影響
      - 線上：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential 原文：'A simple moving average is used as the previous period's EMA in the first calculation.' 以及 'You need far more than 10 days of data to calculate a reasonably accurate 10-day EMA.'
      - 線上：https://raw.githubusercontent.com/TA-Lib/ta-lib/main/src/ta_func/ta_MACD.c 原文：'return TA_EMA_Lookback(optInSlowPeriod) + TA_EMA_Lookback(optInSignalPeriod);' 與 'Seed each price EMA with a simple average of its first 'period' price bars.'；ta_EMA.c 原文：'return optInTimePeriod - 1 + TA_GLOBALS_UNSTABLE_PERIOD(...)'，算得 MACD lookback = 25+8 = 33，第一個有效值在第 34 根
      - 線上：https://blog.csdn.net/u014496893/article/details/122776058 原文：'新股上市首日，其首日DIF为0'、'新股上市首日，其首日DEA为0'、'规定把首日收盘价当成是首日EMA，从次日开始，其EMA开始套用以上公式进行计算'，說明以首根收盤起算是通達信一類軟體的既有慣例
      - 線上：TradingView Pine 的 ta.ema 參考頁 https://www.tradingview.com/pine-script-reference/v5/#fun_ta.ema 由前端動態產生，抓不到內容，未能核對
      - 工作樹：執行前後 git status 都只有原本就存在的 3 個修改檔；本次只寫入 scratchpad\audit\work\verify-emawarm\

### [P3 → P3] 收盤價恰好等於均線時，突破／跌破訊號取決於浮點殘差（部分成立）

- **主張**：交叉條件用嚴格的 > 與 <=。台股價格在跳動單位格點上，收盤價常與 MA 在數學上相等，但 rolling_mean 算出的均線帶 1e-14 的殘差，於是同一個平手情況在 app 與獨立重算間會得到相反的訊號。
- **正確的定義或數值**：平手時的結果穩定且可預期，例如均線先四捨五入到價格精度或加容差再比較。
- **app 實際行為**：平手時的結果由浮點誤差決定，約每萬列發生 8 次。
- **對使用者的影響**：極少數交易日的突破或跌破訊號會多觸發或少觸發一次，回測結果可能因此不穩定；影響輕微。
- **修法建議**：比較前把 MA 四捨五入到小數 4–6 位，或用 close − ma > ε 的容差。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:637-654
  - backend/app/indicators/pipeline.py:1654-1667
- **線上證據**：
  - （無）
- **本地計算證據**：
  - a02_sig.py：全歷史 6,604 列 × 11 個訊號，只有 5 列不一致，全部是平手：3545 在 2024-05-08 close 85.0 與 MA20 85.0（殘差 −1.42e-14）觸發 ma20_breakdown，隔日又因前一日殘差再觸發一次；00885 在 2025-06-27 前一日 close 12.06 等於 MA20 12.06（殘差 1.8e-15）；3545 在 2024-12-02／03 的 ma10_breakout 同理。
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：均線的突破／跌破（pipeline.py:637-654、1654-1667），以及原 finding 沒提到的均線金叉／死叉（622-630、1642-1647），都用嚴格的 > 和 <= 比較 rolling_mean 算出的浮點均線。收盤價在數學上恰好等於均線時，app 的浮點值偶爾帶有 1e-16 到 1.42e-14 的殘差，訊號因此會提早一天觸發，或多觸發、漏觸發一次。全市場歷史 1,423,597 列中，精確平手有 ma5 16,555 次、ma10 5,011 次、ma20 1,545 次，其中 720、182、73 次的浮點值不相等。9 個均線訊號共有 1,457 筆與精確結果不同，分布在 615 檔：453 對是提早一天，278 筆多觸發，273 筆漏觸發。以突破／跌破來算，約每萬列 6.5 次。自選股 9 檔在全部歷史中差異為 0，原 finding 舉的 3545（2024-05-08、2024-12-02/03）和 00885（2025-06-26/27）案例，app 的殘差都是 0.0，訊號正確；那些殘差出自原稽核自己的獨立重算，不是 app。影響僅限選股器、策略、回測用到其他股票時，極少數交易日的訊號時點。修法可以把比較改成整數分，或先把均線四捨五入到價格精度再比較。
    - 理由：原稽核說的機制在 app 的實際輸出裡確實存在：交叉條件用嚴格的 > 和 <=，比較的是 rolling_mean 算出的浮點均線，收盤價恰好等於均線時，訊號觸不觸發取決於殘差的正負。但它當作證據的 5 個自選股案例全都無法重現。這些案例的 −1.42e-14 和 1.8e-15 殘差來自原稽核自己寫的「獨立重算」（A02 的 indep_full.parquet，欄位 i_ma20、i_ma10），不是 app 算出來的。

我的做法：讀取 kline_daily_enriched 全部 661 個分區（1,423,597 列），先確認裡面存的 ma5/10/20/60 和我用 polars rolling_mean().over('symbol') 複製 compute_indicators 的結果逐位元相同，差異 0。接著把收盤價換成整數「分」，用整數滾動和算出精確均線（所有收盤價都落在 0.01 格點上；對照官方 STOCK_DAY 自選股 342 列，收盤價差異 0）。再用 app 同一套交叉公式算出「精確訊號」，和已存的訊號比對。

結果：
(1) 原稽核點名的三個案例，app 存的殘差都正好是 0.0，訊號和精確值一致。3545 在 2024-05-08 的 ma20 − close = 0.0，當天沒有觸發 breakdown，5/09 只觸發一次，這才是正確結果。00885 在 2025-06-26 殘差 0.0，6/27 的 breakout 雙方都是 True。3545 在 2024-12-02 的 ma10 殘差 0.0，12/03 的 ma10_breakout 雙方都是 True。
(2) 自選 9 檔在全部歷史中，9 個均線訊號共 51,994 筆非 null 的「列×訊號」組合，app 和精確值的差異是 0 筆。精確平手分別有 ma5 40 次、ma10 11 次、ma20 5 次，app 的浮點值全部剛好相等，沒有殘差。
(3) 全市場的精確平手中，浮點值不相等的有：ma5 720／16,555、ma10 182／5,011、ma20 73／1,545，殘差介於 1.1e-16 到 1.42e-14。由此產生的訊號差異共 1,457 筆，分布在 615 檔，每一筆都落在平手當天或隔天。其中 922 筆是突破／跌破，另有 535 筆是原 finding 沒提到的均線金叉／死叉（golden_5_20、dead_5_20、golden_20_60，pipeline.py:622-630，同樣是嚴格比較）。1,457 筆中有 453 對（906 筆）是「提早一天觸發」，其餘 551 筆落單：278 筆多觸發、273 筆漏觸發。以突破／跌破來算，約每萬列 6.5 次；原 finding 寫的「每萬列約 8 次」是根據錯的樣本算出來的。

嚴重度維持 P3：使用者的自選股與監控規則都不受影響（規則 0 條），只有選股器、策略、回測用到其他股票時，才會在極少數日子讓訊號位移或多、漏一天。docs/code-review-2026-09-22.md 沒有這項記錄，不算重複回報。盤中路徑 compute_enriched_today 是用部分和 (partial_sum+close)/20 計算均線，平手時殘差怎麼分布我沒有驗證。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:393-401（ma = close.rolling_mean(n).over('symbol')）、:622-654（均線交叉與突破／跌破都用嚴格 >、< 搭配 <=、>=）、:1642-1667（盤中路徑同樣的比較）、:1516-1518（盤中均線用部分和計算）
      - 程式碼：backend/app/taiwan/enriched_refresh.py:81-95（台股 enriched 是對整段 daily 歷史執行 compute_enriched 得出）
      - 本地計算 s2_ties.py（AUDIT_SCRATCH/work/verify-tie/s2.out）：polars 1.40.1；enriched 1,423,597 列；收盤價不在 0.01 格點上的 0 列；已存 ma5/ma10/ma20/ma60 與 rolling_mean 重算結果逐位元不同的列數都是 0
      - 本地計算 s2.out，原稽核點名的案例（已存值）：3545 2024-05-08 close 85.0、ma20 85.0，ma20−close=0.0，當日 breakdown=False、精確值也是 False；5/09 breakdown 為 True/True。00885 2025-06-26 ma20−close=0.0；6/27 breakout 為 True/True。3545 2024-12-02 ma10−close=0.0；12/03 ma10_breakout 為 True/True
      - 對照原稽核的 A02-ma-macd\indep_full.parquet：close−i_ma20 在 3545 2024-05-08 是 −1.4211e-14，在 00885 2025-06-26 是 1.7764e-15；close−i_ma10 在 3545 2024-12-02 是 1.4211e-14。可見 finding 引用的殘差出自它自己的重算，不是 app
      - 本地計算 s3_flips.py（s3.out）：自選股 9 檔、9 個均線訊號，51,994 筆非 null 組合的差異為 0；全市場 12,424,784 筆組合中差異 1,457 筆（615 檔），mismatch_near_tie 等於 mismatch（差異都在平手當天或隔天）；以 app 公式在已存浮點值上重算訊號，與已存訊號的差異 replay_diff=0
      - s3.out，全市場精確平手與浮點殘差：ma5 16,555 次中 720 次不相等、ma10 5,011 次中 182 次、ma20 1,545 次中 73 次；|殘差| 最大 1.4210854715202004e-14，最小 1.1e-16。實例：00405A.TWSE 2026-07-23 close−ma10=−1.7764e-15，已存 ma10_breakdown=True，精確值 False；006206.TWSE 2026-09-15 close−ma5=7.1054e-15，已存 ma5_breakout=True，精確值 False
      - s4_detail.py（s4.out）與後續分類：自選股精確平手 ma5 40 次、ma10 11 次、ma20 5 次（00885 2025-06-26、2454 2025-06-18、3545 2024-05-08、6196 2024-06-04、6196 2025-01-16），浮點值不相等的都是 0；全市場 1,457 筆差異中，453 對（906 筆）是提早一天觸發，其餘 551 筆落單（多觸發 278 筆、漏觸發 273 筆）；突破／跌破 922 筆、均線交叉 535 筆
      - 官方資料對照：reference\csv\twse_stock_day_202608_202609.csv 中自選股 342 列全部能對上 enriched，收盤價差異 0 列，官方收盤價不在 0.01 格點上的 0 列
      - 線上來源 https://docs.python.org/3/tutorial/floatingpoint.html 原文："summing three values of 0.1 may not yield exactly 0.3"（0.1 + 0.1 + 0.1 == 0.3 → False）；比較不精確的值時建議用 math.isclose() 或 round()
      - docs\code-review-2026-09-22.md 中搜尋「浮點|殘差|平手|tie|epsilon|容差」沒有結果，這項不是既有記錄

## 確認正確的項目

- **輸入價格：台股日K店（data/taiwan/daily）的 OHLC 與官方 STOCK_DAY 一致**：讀取官方參考資料 twse_stock_day_202608_202609.csv（10 檔 × 38 個交易日＝380 列），與店內 parquet 逐列 join。指令：cd backend && uv run python work/A02-ma-macd/a02_main.py。結果：店內沒有缺列；close、open、high、low 最大差都是 0.0；Aug–Sep 期間店內也沒有官方沒有的列。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_stock_day_202608_202609.csv
  - 來源：AUDIT_SCRATCH/work/A02-ma-macd/a02_main.out
- **MA5/MA10/MA20/MA30/MA60（全量路徑 compute_indicators）＝收盤價簡單移動平均，不足 n 根時為 null**：程式碼：pipeline.py:392-401 用 rolling_mean(n)，polars 1.40.1 預設 min_samples 等於 n。用純 Python sum/n 在完整店內歷史（2024-01-02 起）獨立重算，與 kline_daily_enriched 比對：Aug–Sep 380 列最大絕對差 7.1e-14，全歷史 6,014–6,564 列最大差 1.1e-13，null 位置零不一致。另外只用官方 CSV 收盤價重算 MA5/10/20/30（分別 340、290、190、90 列），最大差 5.7e-14。第三方比對：鉅亨網 2026-09-23 的 2454 MA5 4,917.00、MA10 4,741.00、MA20 4,531.25、MA60 4,067.17，app 為 4917.0、4741.0、4531.25、4067.1667；6196 鉅亨 535.00、528.00、516.05、518.61，app 相同；0050 鉅亨 110.71、109.05、108.41、105.35（前一日 109.60、108.77、108.08、105.27），app 為 110.71、109.045、108.4125、105.3475（前一日 109.6、108.765、108.085、105.27）。
  - 來源：backend/app/indicators/pipeline.py:392-401
  - 來源：https://www.cnyes.com/twstock/Technical.aspx?code=2454 （2026-09-23：5日均價 4,917.00／10日 4,741.00／20日 4,531.25／60日 4,067.17）
  - 來源：https://www.cnyes.com/twstock/Technical.aspx?code=6196
  - 來源：https://www.cnyes.com/twstock/Technical.aspx?code=0050
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential （"A simple moving average is formed by computing the average price of a security over a specific number of periods."）
- **EMA5/10/20/30/60（全量）：α=2/(n+1)，遞推 EMA_t=α·C_t+(1−α)·EMA_{t−1}，初值取第一根收盤價**：程式碼：pipeline.py:223-224（_ema_alpha）與 402-411（ewm_mean(adjust=False)）。用純 Python 以同樣的首值初始化獨立重算，6,604 列最大差 4.5e-12。StockCharts 的慣例是以前 n 根的 SMA 當初值（原文 "a simple moving average is used as the previous period's EMA in the first calculation"），app 用第一根收盤價，初值做法不同。但店內歷史從 2024-01-02 開始，實測兩種初值在 Aug–Sep 的差距：EMA60 最大 2.5e-7，DIF 為 0，可以忽略。StockCharts 另說 "we calculate back at least 250 periods"，app 有 660 根以上。
  - 來源：backend/app/indicators/pipeline.py:223-224
  - 來源：backend/app/indicators/pipeline.py:402-411
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential
- **MACD DIF 與 DEA（全量）＝EMA12(收盤)−EMA26(收盤)；DEA＝EMA9(DIF)，參數 12/26/9**：程式碼：pipeline.py:412-415、446-457。獨立重算與 enriched 比對：DIF 最大差 1.8e-12，DEA 最大差 1.3e-12（6,604 列）。這與權威定義一致：StockCharts 寫 "MACD Line: 12-day EMA - 26-day EMA; Signal Line: 9-day EMA of MACD Line"，並指出由 Gerald Appel 在 1970 年代後期提出；維基百科中文寫 "DIF=EMA(close,12)−EMA(close,26)；DEM=EMA(DIF,9)"。柱狀體的倍數與畫面標示另見 findings。
  - 來源：backend/app/indicators/pipeline.py:412-415
  - 來源：backend/app/indicators/pipeline.py:446-457
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator
  - 來源：https://zh.wikipedia.org/zh-tw/%E6%8C%87%E6%95%B0%E5%B9%B3%E6%BB%91%E7%A7%BB%E5%8A%A8%E5%B9%B3%E5%9D%87%E7%BA%BF
- **全量路徑的 11 個 MA／MACD 交叉與突破訊號（signal_ma_golden_5_20、ma_dead_5_20、ma_golden_20_60、macd_golden、macd_dead、ma20／ma5／ma10 的 breakout 與 breakdown）**：程式碼：pipeline.py:612-654，條件為「今日 A>B 且昨日 A≤B」。用獨立算出的 MA 與 MACD 自行重算交叉。Aug–Sep 380 列零不一致，期間共觸發 21、13、6、23、17、39、33、47、50、32、31 次。全歷史 6,604 列有 5 列不一致，全部是收盤價恰好等於均線的平手情況，另列為 P3 finding。指令：uv run python a02_sig.py。
  - 來源：backend/app/indicators/pipeline.py:612-654
- **第二套實作一：自選頁 app/taiwan/technical_indicators.py（讀最近 150 個交易日分區）的 MA5/10/20/60 與 MACD**：用 FakeStore 餵真實店內資料，對 2026-09-04～09-23 共 14 天 × 10 檔呼叫 compute_taiwan_daily_indicators，與 enriched 比對。MA 最大差 2.8e-14；MACD DIF、DEA、hist 最大差 0.0030、0.0044、0.0028（相對收盤 ≤1e-6），來自 150 根暖機長度，可以忽略。公式與 pipeline 相同（technical_indicators.py:79-82、106-119）。
  - 來源：backend/app/taiwan/technical_indicators.py:79-119
  - 來源：backend/app/taiwan/watchlist_enrichment.py:138-160
  - 來源：AUDIT_SCRATCH/work/A02-ma-macd/a02_watch.out
- **第二套實作二：台股選股器 _compute_batch_indicators 的 MA5/10/20（最近 35 個交易日）**：以假 self.daily_store 呼叫真實方法，得到 140 列，與 enriched 比對最大差 2.8e-14，零 null。「站上 MA5／MA20」的篩選條件是 close > ma（screener.py:515-523）。
  - 來源：backend/app/taiwan/screener.py:289-329
  - 來源：backend/app/taiwan/screener.py:515-523
- **盤中增量路徑在視窗足夠時的 MA5–MA60、EMA5–EMA30（compute_enriched_today 的部分和與遞推）**：直接以假 self 呼叫真實的 KlineRepository._build_live_agg 與 compute_enriched_today，重現 2026-09-08～09-23 共 12 天 × 10 檔。當 90 個日曆天內有 59 個以上交易日時，MA5～MA60 最大差 1.1e-13，EMA5、EMA10 為 9e-13，EMA20 為 2e-7，EMA30 為 2e-4。EMA60 最大差 0.21（2454，約 3e-4 的價格），原因是記憶體歷史快取只取 300 個日曆天暖機，可以忽略。視窗不足與 MACD 的問題另見 findings。
  - 來源：backend/app/indicators/pipeline.py:1494-1520
  - 來源：backend/app/repository.py:565-570
  - 來源：AUDIT_SCRATCH/work/A02-ma-macd/a02_incr.out
- **前端 K 線均線與自選欄位直接顯示後端的 ma5/ma10/ma20/ma60，標示 MA5 等，與 SMA 口徑一致**：讀程式碼：StockDailyKChart.tsx:73-79 把後端欄位原樣轉成數字；EChartsCandlestick.tsx:13-19 定義欄位；watchlist-columns.ts:40-43 標示 MA5～MA60；primitives.tsx:85-88 以價格格式顯示。沒有啟動畫面實看，只做程式碼驗證。
  - 來源：frontend/src/components/StockDailyKChart.tsx:73-79
  - 來源：frontend/src/lib/watchlist-columns.ts:40-43
  - 來源：frontend/src/components/stock-table/primitives.tsx:85-88

## 無法驗證的項目

- **第三方的 MACD（DIF、訊號線、OSC）與 EMA 數值**：Yahoo 奇摩、HiStock、玩股網、CMoney、聚財網、ifa.ai 的技術指標都由前端動態產生，WebFetch 抓不到數值；鉅亨網技術頁只有均線、沒有 MACD；investing.com 回應 404。唯一線索是一則 WebSearch 摘要：「2454：MACD9 −77.74、DIF −126.97、OSC −49.24」。它的 OSC 等於 DIF−MACD9、沒有乘 2，但原始網址與時間點無法確認（數值不符合任何收盤日，推測是 2026-07-22 盤中快照），所以只當旁證（plausible）。
- **台灣券商或看盤軟體的 MACD 用收盤價還是需求指數 DI＝(H+L+2C)/4**：MoneyDJ 百科用 DI（原文 "Pt=Ct×1/2+Ht×¼+Lt×¼"）；維基百科中文說 "一般可用收盤價代替 DI 值"。各家實際用哪一種抓不到數值可以驗證。實測兩者可能正負號相反，例如 3034 在 2026-09-23 以收盤價算 OSC＝−0.205，以 DI 算＝+0.148。
- **vol_ma5／vol_ma10（量均線）**：不在 A02（價格均線）範圍。附帶觀察：店內 volume 與官方 STOCK_DAY 有 16/380 列不同，而且差值都是整千股（例如 2330 在 2026-09-18 差 3,702,177 股），請交給成交量領域覆核。
- **ema5～ema60 的畫面呈現**：K 線資料介面（EChartsCandlestick.tsx 的 OHLC）沒有 ema 欄位，只有後端 enriched 與自訂訊號可能用到。沒有啟動 app，無法確認畫面上有沒有地方顯示；數值本身已在 verified_ok 驗證。
- **未還原價對回測報酬的實際影響**：禁止執行 run_pipeline 或回測服務，沒有實跑。依 0050 在 2025-06-18 的收盤 188.65→47.57 推測，跨這天的回測會記成約 −75% 的假虧損（推測）。
