# G03-gap｜G3-research-context-compare-detail

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 9 | 1 | 0 | 0 | 0 | 14 | 8 |

## 稽核者摘要

結論：研究脈絡的公式本身正確，出錯的是餵給它的資料和日期基準。

- 公式比對：自選 9 檔 × 17 個交易日（9/1～9/23），共 153 組。拿 app 實際輸出（在隔離的資料副本上跑真實的 get_research_context）對照我用官方 STOCK_DAY 獨立重算的值，以下欄位全部 0 差異：close、previous_close、change、change_pct、return_20d、high_20d／low_20d、兩個 20 日距離、ma5、ma20、distance_to_ma20、above_ma5／above_ma20。ma60 共 18 組，也與 store 重算一致。9/23 的漲跌停價與交易所 MIS 官方的 u／w 逐一相同。

新發現 10 項：1 項 P1、3 項 P2、6 項 P3。

- P1：日線 store 的 9/16、9/23 兩個分區完全沒有上櫃資料（0 列）。原因是 16:30 抓櫃買失敗被吞掉，21:40 補跑又判定「已是最新」。結果：
  - 上櫃個股 9/23 的價格欄全部 MISSING，9/17 沒有昨收與漲跌停。
  - 9/22 的 20 日窗口平移，例如 6488 的 return_20d：app −3.86%，官方 −0.92%。
  - 9/23 大盤統計只有上市（漲 538、跌 647、成交值少了櫃買約 2,532 億），卻標為 complete，照樣送進 AI。
- P2（第 1 項）：個股詳細頁「所屬大盤基準」卡永遠顯示 --。原因是 detail_service 以空陣列呼叫 parse_*_rows。
- P2（第 2 項）：日曆沒有國定假日資料，每天 16:00 起到 16:30 日線更新寫入前，研究脈絡的目標日都不在 store。後果：
  - 價格欄全部 MISSING。
  - previous_close 與漲跌停價改用前兩個交易日的收盤。
  - 均線與 20 日高低其實是前一交易日的值，卻標成目標日。
  - 目標日若是 9/25 中秋這類平日假日，影響時間更長：9/25 16:00 起到 9/29 16:00 共 4 天，研究卡、比較頁、AI 都拿不到價格。
- P2（第 3 項）：store 的成交量／值在當天 16:30 前後寫入，比官方最終值少。9/16～9/23 的 16 筆中，2330 最多少 9.05%，6196 在 9/17 少 16.95%；隔天才寫入的分區則完全一致。
- P3：
  - ETF 的 tick_size 套用股票升降單位：0050 給 0.5，實際 0.05；00878 給 0.05，實際 0.01。
  - 20 日高低以收盤價計算卻沒有標註；多數工具（如 Donchian 通道）用的是最高價／最低價。
  - 融資尚未公布時，AI 證據整段丟掉大盤與產業脈絡。
  - 證據計數與狀態標籤是寫死的。
  - parse_taiex_rows 把第一列的昨收設為開盤價，屬於潛在錯誤。
  - 比較頁把缺值一律顯示為「不適用」。

第三方比對：只有 HiStock 抓得到，其 3 日、一週、兩週漲跌幅的基準日算法與 app 相同。Yahoo 與玩股網的技術面數值由前端動態產生，Goodinfo 回 403。WebSearch 額度已用完。

## 問題清單

### [P1 → P1] 日線 store 9/16、9/23 缺整個上櫃市場，研究脈絡、比較頁與 AI 對上櫃標的與大盤統計靜默出錯，卻標為 complete（確認成立）

- **主張**：每日快照抓櫃買失敗時只記 warning，仍把只有上市的資料寫成當日分區；之後的補跑以 store 的最大日期判斷是否已最新，所以永遠不會補回上櫃。研究脈絡以全庫日期當窗口，上櫃標的因此出現缺值與 20 日窗口平移，大盤統計只剩上市。
- **正確的定義或數值**：當日分區應包含上市與上櫃；任何一個市場抓取失敗時，不應寫入該日分區（或應標為 partial 並讓後續排程補抓）。研究脈絡與大盤統計在資料不完整時應標 partial／missing。
- **app 實際行為**：兩個交易日的上櫃完全缺失且永久不補。上櫃標的：9/17 沒有昨收與漲跌停，9/22 的 20 日報酬、均線、20 日高點以錯誤窗口計算，9/23 價格欄全缺。9/23 的大盤漲跌家數與成交值只含上市，標為 complete 並送進 AI 證據。
- **對使用者的影響**：自選 9 檔都是上市股，個股欄位不受影響；但使用者看到的 9/23 大盤脈絡、AI 報告的 market_context.advance_ratio（0.407）與 market_turnover 少了約 43% 的標的與 2,532 億成交值。任何上櫃標的放進比較頁或詳細頁，都會拿到缺值或錯誤窗口的 20 日報酬與均線。
- **修法建議**：fetch_date 在任一市場失敗時拋錯或回傳缺失市場清單，refresh_dates 不寫入不完整的分區；補跑改以「每個分區的各交易所列數」判斷缺漏，並對 9/16、9/23 用 force 補抓上櫃。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:112-123（TPEx 例外被吞掉，只回傳 TWSE frame）
  - backend/app/taiwan/daily_refresh.py:167-176（日期已存在就 skip，非空即寫入）
  - backend/app/taiwan/daily_update.py:282（缺漏範圍從 d_max 起算）
  - backend/app/taiwan/research_context.py:376-392、419-447（以全庫日期決定 prev_date 與窗口，closes[-21] 以列數計）
  - backend/app/taiwan/market_intelligence.py:246-254、576-582（大盤統計直接加總 store，daily 分區存在即為 current）
- **線上證據**：
  - 依規定不得直連櫃買中心，改用已下載的官方資料：reference/csv/tpex_daily_quotes_20260923.csv（主檔內上櫃 1,007 檔：漲 432、跌 427、成交值 253,232,230,458 元）
  - reference/csv/tpex_trading_stock_202608_202609.csv（6488：9/22 收 971、8/25 收 980 → 20 日報酬 −0.92%）
- **本地計算證據**：
  - 實際 data/taiwan/daily 分區各交易所列數：9/15 為 TWSE 1330＋TPEX 1006；9/16 為 TWSE 1330＋TPEX 0；9/22 為 1331＋1009；9/23 為 TWSE 1330＋TPEX 0
  - data/backend.log 2026-09-23 16:30:05 WARNING：TPEx snapshot fetch failed for 2026-09-23: peer closed connection…；21:40:10 記錄 daily=success、is_fully_current=True
  - rc_audit2_out.json：6488.TPEX 在 9/22 的 return_20d，app −3.86%／官方 −0.92%；ma5 934.4／939.0；ma20 945.6／943.45。9/17 的 previous_close、change、limit_up 皆為 null。9/23 的 close 為 null（MISSING），但 overall_status＝complete
  - rc_audit2_out.json：market_context 9/23 為漲 538、跌 647、平 137、turnover 887,250,849,184，等於上市 exchange_turnover，status＝complete；9/22 為漲 795、跌 1289、平 215、turnover 1,339,555,264,362
  - coverage.py：最近 65 個分區中，989 檔上櫃只有 63 列，缺的正是 9/16 與 9/23
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：成立，只需一處限定。TaiwanOfficialDailySnapshotAdapter.fetch_date 抓櫃買失敗時只記 warning（9/23 是 HTTP 200 但 body 不完整），仍把只有上市的資料寫成當日分區，refresh_dates 也回報成功。之後的排程與 UI 更新以 store 最大日期與分區是否存在判斷缺漏，所以 9/16、9/23 的上櫃不會被自動補回；只能手動跑 CLI `python -m app.taiwan.daily_update --force --target-date <日期>` 或 `--symbols` 補救，因此「永久」應改成「沒有任何自動或 UI 途徑補回」。研究脈絡以全庫日期決定 prev_date，並以列數取 closes[-21]，上櫃標的因此出現以下錯誤：9/17 昨收與漲跌停為空；9/22 的 20 日報酬為 −3.86%（官方口徑 −0.92%），均線同樣算錯；9/23 價格全缺，卻都標 complete。9/23 的大盤漲跌家數與成交值只含上市，少了 1,007 檔上櫃（占 43%）與 2,532 億成交值（占合計 22%），使日成交值看似下滑 33.8%（實際約 14.9%），並以 complete 送進 AI 證據。補充影響：自選 2454 的產業脈絡（半導體業）9/23 也只由上市成員彙總並標 complete；enriched 快取的 9/16、9/23 分區同樣缺上櫃。9/16 缺漏的原因因 log 已輪替，屬推測。
    - 理由：我逐一讀了 finding 引用的程式碼，行號和現況一致。也用唯讀方式重跑了 app 的純讀取服務，並拿官方參考資料和另一個獨立線上來源（Yahoo Finance、Wikipedia）比對。finding 的主張全部重現，嚴重度 P1 恰當，只有「永久不補」需要加限定。

程式碼路徑：
(1) snapshot_provider.fetch_date 在 TPEx 抓取丟出例外時，只記一筆 warning（:118），接著把只有 TWSE 的 frame 合併後回傳（:120-124）。
(2) refresh_dates 拿到非空的 df 就寫入分區，且不回報 failed_dates（:173-178）；下次遇到同一天，只要分區已存在就 skip（:167）。
(3) run_update 從 d_max 起算缺漏範圍（:282）。9/23 16:30 那次跑完，21:40 再跑時直接判為「Daily store already current」。
(4) 排程 daily_pipeline.py:149 不帶 force；UI 的 update_to_latest 也只看最新日期。唯一能補回的是手動 CLI：--force 搭配 --target-date，或用 --symbols 走逐檔路徑。
(5) boot 完整性檢查只看 kline_* 表的分區是否存在，也抓不到這個缺口。

實際資料：9/16、9/23 兩個分區只有 TWSE 1330 列、TPEX 0 列，其餘日期約 1006-1009 列。backend.log 顯示 2026-09-23 16:30:03 TPEx 回應 200，16:30:05 出現 warning「TPEx snapshot fetch failed … incomplete chunked read」，16:30:33 記 daily=success；21:40:10 記 daily=success、is_fully_current=True。9/16 的 log 已輪替不見（backend.log.2 不存在），所以 9/16 的原因是推測，依據是分區 mtime 16:30:22 與排程時間吻合；但資料缺口本身已確認。

獨立線上來源：Yahoo Finance 的 6488.TWO 顯示 9/16 收 937、9/23 收 950，證明兩天都是上櫃交易日。缺值是漏抓，不是休市。Wikipedia 的 ROC 定義是「(close today − close N days ago) / close N days ago」，N 以交易日計。app 的 closes[-21] 是先 drop_nulls 再按列數取，少一天就會多回溯一個交易日。

以 app 服務重算的結果與 finding 完全一致：
- 6488.TPEX 9/22：return_20d −3.8614%，官方口徑 971/980−1 = −0.9184%；ma5 934.4 對官方 939.0；ma20 945.6 對官方 943.45。
- 6488.TPEX 9/17：previous_close、change、limit_up 都是 None。
- 6488.TPEX 9/23：close 為 None（classification MISSING），但 overall_status 是 complete。
- 大盤 9/23：漲 538、跌 647、平 137，成交值 887,250,849,184，等於 TWSE 的 by_exchange 成交值；tpex rows 0；status complete。data_quality 雖有 missing_symbols_count=1047，但不影響 overall_status。
- 官方 9/23 上櫃（主檔內 1007 檔）：漲 432、跌 427，成交值 253,232,230,458。缺掉的上櫃標的占上市加上櫃合計 2337 檔的 43.1%，成交值占合計的 22.2%。

finding 沒提到、但我查到的面向：
(a) 自選的 2454.TWSE 雖然個股欄位正確，但 9/23 的 industry_context（半導體業）也由同一個 store 彙總，成交值從 9/22 的 567.0B 掉到 328.7B，仍標 complete。這對自選股是可見影響。成員差異沒有逐一核對，歸因於上櫃缺漏屬推測。
(b) app 顯示的 9/23 大盤成交值比 9/22 少 33.8%，但把官方上櫃成交值補回來後只少約 14.9%，會誤導 AI 對量能的判讀。
(c) kline_daily_enriched 的 9/16、9/23 分區也只有 TWSE（1319／1323 列），缺口會傳到策略與選股快取。
(d) 下一個交易日的上櫃漲跌家數會因為 prev_close 缺失，被歸成 uncompared。這是依 market_intelligence.py 的 left join 推論，屬推測，未實測。

口徑補充：台灣習慣上「大盤」常只指上市。但本 app 的 market_totals 在 9/22 明確涵蓋上市加上櫃（universe 2377 檔），9/23 等於悄悄換了口徑，並標為 complete。所以這個缺陷在 app 自身的定義下成立。

嚴重度：這是錯誤的金融口徑，而且會以「complete」送進 AI 證據與比較頁，自動流程無法自癒，符合 P1。資料本身沒有被破壞，也不直接產生交易結果，所以不到 P0。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-124：TPEx 抓取的 try 區塊吞掉例外，只記 logger.warning（:118），之後 concat 剩下的 frames 並回傳
      - backend/app/taiwan/daily_refresh.py:150,167-178：日期已在 available_dates 就 skip；df 非空就 write_batch，並記為 dates_fetched、不記入 failed_dates
      - backend/app/taiwan/daily_update.py:260,282：d_range 以 d_max 起算；排程 backend/app/jobs/daily_pipeline.py:149-153 不帶 force；只有 CLI main（daily_update.py:445,458-463）提供 --force／--symbols
      - backend/app/taiwan/research_context.py:376,385,390,419-427：prev_date 取全庫 available_dates[-2]；closes 先 drop_nulls 再取 closes[-21]；research_context.py:766-773 的 overall_status 只看三個 dataset 的最大日期
      - backend/app/taiwan/market_intelligence.py:246（turnover 直接加總）、394-395（算了 missing_count 卻不參與判定）、576-581（三個 dataset 都 current 就判 complete）
      - 本地分區列數（自寫的 polars 腳本 parts.py）：date=2026-09-16 {'TWSE':1330}，mtime 2026-09-16T16:30:22；date=2026-09-23 {'TWSE':1330}，mtime 2026-09-23T16:30:05；9/15 {'TPEX':1006,'TWSE':1330}；9/22 {'TPEX':1009,'TWSE':1331}
      - data/backend.log 第 37257-37265 行：16:30:03 TPEx dailyQuotes『HTTP/1.1 200 OK』；16:30:05『TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)』；16:30:33『daily=success』。第 45477 行 21:40:10『daily=success … is_fully_current=True』
      - 唯讀重跑 TaiwanStockResearchContextService.get_research_context('6488.TPEX')（verify.py）：9/17 previous_close=None、change=None、limit_up=None、overall=complete；9/22 return_20d=-0.038614、ma5=934.4、ma20=945.6；9/23 close=None、classification MISSING、overall=complete
      - 官方參考資料 reference/csv/tpex_trading_stock_202608_202609.csv：6488 在 9/22 收 971.0，往前 20 個交易日（2026-08-25）收 980.0，ret20=-0.009184，ma5=939.0，ma20=943.45
      - 唯讀重跑 TaiwanMarketIntelligenceService.get_snapshot：9/23 totals 538/647/137，turnover 887250849184.0，twse turnover 887250849184.0，tpex rows 0，status complete，missing_symbols_count 1047；9/22 為 795/1289/215、1339555264362.0，tpex rows 1009
      - 官方 reference/csv/tpex_daily_quotes_20260923.csv，篩成 security master 內 active 的 stock／etf：1007 檔，漲 432、跌 427、平 119，amount 合計 253232230458；列數占比 43.09%，成交值占比 22.20%
      - 獨立線上來源 Yahoo Finance https://query1.finance.yahoo.com/v8/finance/chart/6488.TWO?period1=1789488000&period2=1790294400&interval=1d：timestamp 1789520400（2026-09-16 09:00 +08）close 937.0，1790125200（2026-09-23 09:00 +08）close 950.0。證明兩天上櫃有交易，與官方資料一致
      - 定義來源 https://en.wikipedia.org/wiki/Momentum_(technical_analysis)：『Rate of change = (close today − close N days ago) / close N days ago』，N 為交易日數
      - 新面向（自寫的 ind.py）：2454.TWSE 的 industry_context（半導體業）9/22 turnover 567039950053.0，9/23 為 328659613799.0，兩天 status 都是 complete；enr.py：kline_daily_enriched 的 9/16 {'TWSE':1319}、9/23 {'TWSE':1323}，9/15 {'TPEX':987,'TWSE':1324}
      - 未被既有文件記錄：docs/code-review-2026-09-22.md 以 grep 搜 fetch_date／snapshot_provider／TPEx snapshot 均無命中
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：原 finding 成立，所有數值都已重現；以下是補充與精確化。

問題機制：每日快照在櫃買抓取失敗時只記 warning，仍把只有上市的資料（TWSE 1330 列、TPEX 0 列）寫成 9/16 與 9/23 的分區。之後各條補跑路徑（daily_update 的 d_max、bootstrap 的 latest+1、refresh_dates 跳過已存在日期）都只看日期、不看各交易所列數，所以永遠不會補回上櫃。9/23 有 log 證據；9/16 的 log 已輪替掉，原因由分區修改時間推定。

研究脈絡與比較頁：所有上櫃標的都受影響。
- 9/16 與 9/23 當天沒有價格。
- 9/17 沒有昨收、漲跌與漲跌停價。
- 之後的 20 日報酬、均線、20 日高低點都以平移過的窗口計算。以 6488 在 9/22 為例，20 日報酬本地 −3.86%、官方 −0.92%；ma5 本地 934.4、官方 939.0；ma20 本地 945.6、官方 943.45。
- 資料品質仍標 complete。

大盤統計：9/23 只有上市（漲 538、跌 647、平 137，成交值 8,872.5 億），AI 證據的 advance_ratio 為 0.407，少了 1,007 檔上櫃（佔 43.1%）與 2,532 億成交值。另外，9/17 的上櫃 1,006 檔因缺前日收盤全部無法比較，當天漲跌家數也只含上市。

自選 9 檔都是上市股，個股欄位不受影響。
    - 理由：我用官方參考資料和本地 parquet 各寫了一支腳本重算，finding 列出的數值全部逐一重現，程式碼路徑也逐行核對無誤。

1. 分區內容：9/16 與 9/23 兩個分區只有上市股（TWSE 1330 列、TPEX 0 列）。其他日期都同時有上市與上櫃，例如 9/15 為 1330＋1006，9/22 為 1331＋1009。
2. 寫入流程：snapshot_provider.fetch_date 在 TPEx 抓取失敗時只記一筆 warning，照樣回傳只有上市的資料；refresh_dates 只要結果非空就寫入分區。backend.log 在 9/23 16:30:05 有 TPEx 失敗的 warning，同一輪與 21:40 那輪都記錄 daily=success。
3. 不會補回：daily_update 以 d_max 起算缺漏範圍，bootstrap 用 latest+1，refresh_dates 又會跳過已存在的日期，沒有任何路徑會依交易所列數補抓。兩個分區的修改時間仍停在當天 16:30，之後沒有被改寫過。
4. 研究脈絡：以 6488.TPEX 為例，本地與官方逐項並列如下，本地數字與 finding 完全一致。
   - 9/22 的 20 日報酬：本地 −3.8614%（基準日 8/24，收盤 1010）；官方 −0.9184%（基準日 8/25，收盤 980）。
   - 9/22 的 ma5：本地 934.4，官方 939.0。
   - 9/22 的 ma20：本地 945.6，官方 943.45。
   - 9/17 的昨收與漲跌為 null；因為昨收是 null，程式也不會算漲跌停價。
   - 9/23 的收盤為 null，但 overall_status 只看 daily、法人、融資三個 store 的最大日期，所以仍標 complete。
   - 8069 在 9/17 的 20 日高點：本地 163，官方 160.5。這證明窗口平移確實會改變數值。
5. 大盤統計（9/23）：本地重算得漲 538、跌 647、平 137，成交值 887,250,849,184，漲跌比 538/1322＝0.407，與 finding 一致。官方上櫃在主檔範圍內共 1,007 檔（漲 432、跌 427、平 119，成交值 253,232,230,458），佔 2,337 檔的 43.1%。
6. 另外看到 finding 沒寫到的面向：9/17 的大盤統計中，1006 檔上櫃因為缺前日收盤全部被歸為「無法比較」，當天漲跌家數實際上也只有上市。9/16 當天的統計同樣只有上市。
7. 比較頁經 comparison.py 呼叫 get_research_context，所以同樣受影響。
8. 本地收盤價與官方 6488、8069 在 8 到 9 月逐日比對，共 76 列、0 筆不符；缺的只有 9/16 和 9/23。
9. 最近 65 個分區中，989 檔上櫃各只有 63 列，上市股則以 65 列為主（1305 檔）。

限制與推論：9/16 的 log 已經輪替掉（backend.log.3 從 9/17 開始），所以 9/16 的缺漏原因只能從分區修改時間 9/16 16:30:22 與排程時間吻合來推定，屬推測。

嚴重度：這屬於靜默的金融口徑錯誤，而且被標成 complete 送進 AI 證據，也不會自動修復，符合 P1。自選 9 檔都是上市股，個股欄位不受影響；受影響的是大盤脈絡和任何上櫃標的。
    - 證據：
      - 本地計算 p1_partitions.py 的輸出：date=2026-09-16 {'TWSE': 1330}，修改時間 09-16 16:30:22；date=2026-09-23 {'TWSE': 1330}，修改時間 09-23 16:30:05；date=2026-09-22 {'TPEX': 1009, 'TWSE': 1331}；date=2026-09-15 {'TPEX': 1006, 'TWSE': 1330}
      - backend/app/taiwan/providers/snapshot_provider.py:112-123：TPEx 例外只 logger.warning，frames 仍只有 TWSE 就回傳
      - backend/app/taiwan/daily_refresh.py:150,167-176：日期在 available 裡就 skip；df 非空就 write_batch
      - backend/app/taiwan/daily_update.py:282 與 141：d_range 從 d_max+1 起算；bootstrap.py:392-397、446-448 同樣用 latest+1，沒有依交易所列數的修復路徑
      - data/backend.log:37258：2026-09-23 16:30:05 WARNING TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body；:37265 記錄 daily=success；:45477 在 21:40:10 記錄 daily=success、is_fully_current=True
      - backend/app/taiwan/research_context.py:376-392、419-447：prev_date=available_dates[-2]，closes[-21] 以列數計；:767-773 的 overall_status 只看三個 store 的最大日期
      - 本地計算 p2_rc.py，6488.TPEX @9/22：本地 ret20=-0.038614（基準日 2026-08-24，收盤 1010）、ma5=934.4、ma20=945.6；官方 ret20=-0.009184（基準日 2026-08-25，收盤 980）、ma5=939.0、ma20=943.45（來源 reference/csv/tpex_trading_stock_202608_202609.csv）
      - 本地計算 p2_rc.py，6488.TPEX @9/17：本地 prev_close=None、change=None；官方為 937→912，漲跌 -25。@9/23：本地 close=None；官方 close 950。8069.TPEX @9/17：本地 h20=163.0，官方 160.5
      - backend/app/taiwan/research_context.py:728-736：p_close 為 None 時 limit_up、limit_down 保持 None
      - 本地計算 p3_breadth.py @9/23：total rows=1330、adv=538、dec=647、flat=137、turnover=887250849184，TPEX rows=0；AI 的 advance_ratio=538/1322=0.407（ai_research.py:141-147）
      - 官方 reference/csv/tpex_daily_quotes_20260923.csv 在主檔範圍內：1007 檔，adv 432、dec 427、flat 119，amount 253232230458；上櫃佔 1007/2337=43.1%
      - 本地計算 p3_breadth.py @9/17（finding 未提）：TPEX 1006 列全部 uncompared（prev_close 為 null），總 uncompared=1017，漲跌家數只含上市
      - 本地計算 p4_cov.py：最近 65 個分區（2026-06-24～09-23）中 989 檔上櫃各 63 列，上市以 65 列為主（1305 檔）；6488 與 8069 官方 76 列對本地收盤價 0 筆不符，缺的只有 9/16 與 9/23
      - backend/app/taiwan/comparison.py:211-216：比較頁逐檔呼叫 research_svc.get_research_context
      - 腳本位置：AUDIT_SCRATCH/work/verify-g3tpex/p1_partitions.py、p2_rc.py、p3_breadth.py、p4_cov.py
      - 推測：9/16 的缺漏原因沒有 log 可查（backend.log.3 從 2026-09-17 11:14 開始，backend.log.2 不存在），只能從分區修改時間 09-16 16:30:22 與 16:30 排程吻合來推定

### [P2 → P2] 個股詳細頁「所屬大盤基準」卡永遠顯示 --（以空陣列解析指數）（確認成立）

- **主張**：_aggregate_market_context 以空陣列呼叫 parse_taiex_rows([])／parse_tpex_rows([])，結果永遠是空，每一檔股票都回 status=unavailable，畫面的大盤收盤與漲跌幅恆為 --。
- **正確的定義或數值**：顯示所屬指數的最新收盤與漲跌幅（例：9/23 加權指數 48,157.29，+0.75%）。
- **app 實際行為**：所有個股（包括自選 9 檔）的大盤基準卡都是「-- / --」。
- **對使用者的影響**：使用者每次打開個股詳細頁都看不到大盤對照，這項功能等於不可用。
- **修法建議**：改讀本機指數日線 store（以唯讀方式讀 parquet，避免 read_range 觸發回補寫檔），取最後兩筆計算漲跌。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/detail_service.py:595-626
  - frontend/src/pages/TaiwanStockDetail.tsx:436-448
- **線上證據**：
  - 官方 TAIEX 9/23 收盤 48,157.29、9/22 收盤 47,800.17（reference/raw/twse_taiex_mi_5mins_hist_202609.json），代表資料存在
- **本地計算證據**：
  - rc_audit2_out.json（detail_market_context）：TWSE 與 TPEX 皆為 close=null、change_pct=null、meta.status=unavailable、source=index_provider
  - 本機已有指數日線 store：data/taiwan/index_daily（519 個分區，最新 9/17）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立：TaiwanStockDetailService._aggregate_market_context（detail_service.py:597-600）固定以空陣列呼叫 parse_taiex_rows([])／parse_tpex_rows([])，結果恆為空。每一檔個股（含自選 9 檔，全部走 TAIEX 分支）的 market_context 都回 close/change/change_pct=null、status=unavailable，個股詳細頁「所屬大盤基準」卡恆顯示「-- / --」。這段程式碼從初始提交就是這樣。嚴重度 P2 恰當。修法補充：本機 index_daily store（代號 t00.TWSE／o00.TPEX）最新只到 9/17，而且沒有 change 欄位，改讀 store 會顯示 9/17 收盤 46,288.0，不是 9/23 的 48,157.29（+0.75%）。修正時要自行用前一筆收盤計算漲跌，並標示資料日期或 stale 狀態，或者先確保 store 已更新。
    - 理由：我讀了現行程式碼並實際執行，finding 成立。_aggregate_market_context 在 detail_service.py:597-600 固定以空陣列呼叫 parse_tpex_rows([])／parse_taiex_rows([])。這兩個函式（index.py:62-120、122-176）只會逐列轉換傳入的資料，傳入空陣列就回傳空清單，所以 `if indices:`（detail_service.py:601）永遠不成立，程式會落到 619-626 的 unavailable 分支：close、change、change_pct 都是 None。git blame 顯示這幾行從初始提交 ^fd79b63 以來就沒變過，沒有其他地方覆寫這個結果：API 路由 app/api/taiwan.py:285-287 原樣回傳，單例 detail_service.py:714-717 用的也是真正的 TaiwanIndexProvider。前端 TaiwanStockDetail.tsx:442 寫 `close?.toFixed(2) || '--'`，444-446 在 change_pct 為 null 時顯示 '--'，所以畫面上恆為「-- / --」。自選 9 檔都是 .TWSE，會走 TAIEX 分支，全部受影響。

定義方面，Wikipedia（英文）與維基百科「櫃買指數」條目都指出：加權指數涵蓋上市股票，櫃買指數涵蓋上櫃普通股。程式碼依交易所選 TAIEX 或 TPEX_INDEX 的對應是對的，錯在沒有餵資料。Yahoo 奇摩股市（與 finding 使用的官方檔不同的來源）顯示 9/23 加權指數收盤 48,157.29、漲 357.12 點（+0.75%），與官方參考檔一致，證明資料實際存在。

嚴重度：這是一張資訊卡完全失效，個股詳細頁其他區塊不受影響，也不影響交易或資料正確性，所以 P2（明確的功能錯誤）恰當，不到 P1。已知問題清單 docs/code-review-2026-09-22.md 沒有記錄這一項，用 grep 搜尋 benchmark、大盤、index_provider、parse_taiex 都沒有相關條目。

補充一個 finding 沒提到的細節，只影響修法，不影響成立與否：本機 index_daily store 最新只到 9/17，代號是 t00.TWSE／o00.TPEX，而且沒有 change 欄位。照 fix_suggestion 改讀本機 store，會顯示 9/17 的收盤 46,288.0，不是 expected 寫的 9/23 48,157.29。要不就先把 store 更新，要不就在卡片標示資料日期與 stale 狀態，漲跌則要自己用前一筆收盤計算。
    - 證據：
      - backend/app/taiwan/detail_service.py:597-600：`indices = self.index_provider.parse_tpex_rows([])`／`indices = self.index_provider.parse_taiex_rows([])`，固定傳入空陣列
      - backend/app/taiwan/enrichment/index.py:67-120：parse_taiex_rows 只對傳入的 rows 做 for 迴圈，沒有自己抓資料或讀 store；parse_tpex_rows（122-176）同理
      - backend/app/taiwan/detail_service.py:619-626：fallback 回傳 close=None, change=None, change_pct=None, meta.status=unavailable, source=index_provider
      - backend/app/taiwan/detail_service.py:161、71、714-717 與 app\api\taiwan.py:285-287：預設單例使用真正的 TaiwanIndexProvider，路由原樣回傳，沒有其他覆寫路徑
      - frontend/src/pages/TaiwanStockDetail.tsx:442-446：close 為 null 時顯示 '--'，change_pct 為 null 時也顯示 '--'
      - git blame -L 595,601 backend/app/taiwan/detail_service.py → 全部是 ^fd79b63（nanachi1212 2026-09-11），從初始提交就是這樣
      - 本地實跑（uv run python scratchpad/audit/work/verify-G3-mktctx/run.py，以 SimpleNamespace(index_provider=TaiwanIndexProvider()) 當 self 呼叫 _aggregate_market_context）：TWSE → {close: null, change: null, change_pct: null, meta.status: unavailable, source: index_provider}；TPEX 結果相同；parse_taiex_rows([]) -> []、parse_tpex_rows([]) -> []
      - 官方參考檔 reference/csv/index_daily_202608_202609.csv：TAIEX 2026-09-22 close 47800.17、2026-09-23 close 48157.29；TPEX_INDEX 2026-09-23 close 413.76、change -1.41。(48157.29-47800.17)/47800.17*100 = 0.7471%
      - 線上（與 finding 不同來源）https://tw.stock.yahoo.com/quote/%5ETWII：「成交48,157.29」「漲跌357.12(0.75%)」「收盤 | 2026/09/23 13:31 更新」
      - 線上定義 https://en.wikipedia.org/wiki/Taiwan_Capitalization_Weighted_Stock_Index："The TAIEX covers all of the listed stocks excluding preferred stocks, full-delivery stocks and newly listed stocks..."；https://zh.wikipedia.org/wiki/櫃買指數：櫃買指數採樣「符合掛牌時間且非屬管理股票、興櫃股票之所有上櫃公司普通股」，與加權指數（上市）對應。證實依交易所選 TAIEX／TPEX_INDEX 的對應正確
      - 本機 store 唯讀讀取（polars.read_parquet data/taiwan/index_daily/date=2026-09-1[5-7]/part.parquet）：欄位只有 symbol,date,open,high,low,close,volume,amount,quote_ts（沒有 change）；t00.TWSE 9/15 45511.49、9/16 45848.9、9/17 46288.0；o00.TPEX 9/17 398.17；共 519 個分區，最新是 date=2026-09-17
      - docs/code-review-2026-09-22.md 以 grep 搜尋 benchmark|大盤|index_provider|parse_taiex 都找不到這個問題，不屬於已知項目；操作說明書.md:332 說明這張卡是「所屬大盤基準 — 用於相對強弱計算的基準指數」
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立，不需要更正。_aggregate_market_context（detail_service.py:597-600）把寫死的空陣列傳給 parse_taiex_rows 和 parse_tpex_rows，所以不管哪一檔、哪個市場，都回傳 close、change、change_pct 為 null、status=unavailable；前端「所屬大盤基準」卡因此永遠顯示「-- / --」。官方 9/23 加權指數為 48,157.29、+357.12、+0.75%，櫃買指數為 413.76、-1.41、-0.34%，把同樣的資料餵給 provider 可以正確算出這些數字，所以問題只出在傳入空陣列。

修法建議有兩點需要補充：
- 本機 store 的代號是 t00.TWSE／o00.TPEX，要做代號對應。
- store 目前最新只到 2026-09-17（t00.TWSE 46,288.00、+0.96%）。直接讀 store 會顯示落後 4 個交易日的數值，所以還要處理資料過期標示，或讓資料能及時更新。
    - 理由：這條 finding 成立，重現出來的數字和 finding 一致。

程式碼部分：detail_service.py:597-600 把寫死的空陣列傳給 parse_tpex_rows([]) 和 parse_taiex_rows([])。這兩個解析函式（enrichment/index.py:71、:131）的主體是 `for r in rows`，傳入空陣列一定回傳 []，所以 `if indices:` 永遠不成立，函式一律落到 :619-626 回傳 close/change/change_pct=None、status=unavailable。git log -L 顯示這段自 fd79b63 引入後沒改過，`git diff HEAD` 顯示工作樹也沒有未提交的修改。整個 backend 只有 detail_service.py:161 一處呼叫，API（app/api/taiwan.py:287）拿到後直接回傳，中間沒有別的程式覆寫 market_context。前端 TaiwanStockDetail.tsx:442 寫 `close?.toFixed(2) || '--'`，:444-446 在 change_pct 為 null 時顯示 '--'。這張卡片包在 :358 的 `data && !isLoading` 條件裡，嵌入模式和一般頁面都會顯示，所以所有個股都是「-- / --」。

數據重現：我寫了 repro.py，用假的 self 呼叫未綁定的 _aggregate_market_context。這樣做是為了不實例化 service，以免觸發即時行情或資料寫入。TWSE 和 TPEX 兩個分支都回傳 close=null、change=null、change_pct=null、meta.status=unavailable、source=index_provider，和 finding 的本地證據完全相同。

我也把官方參考資料的原始列直接餵給同一個 provider：TAIEX 算出 2026-09-23 close=48157.29、change=+357.12、change_pct=0.75；TPEx 算出 close=413.76、change=-1.41、change_pct=-0.34。和官方 MI_INDEX 9/23 的「發行量加權股價指數 48157.29, +, 357.12, 0.75」完全吻合。所以問題只出在傳了空陣列，解析器本身沒問題。

本機指數 store 有 519 個分區，最新一天是 2026-09-17：t00.TWSE 為 46288.0（前一日 45848.9，+439.10，+0.96%），o00.TPEX 為 398.17（-1.04，-0.26%），兩者都和官方 9/17 的數值相符。

finding 的主張、嚴重度 P2（明確的功能錯誤：整張卡片沒有作用）與實際影響都正確。docs 裡查不到任何先前的記錄，所以不是重複回報。

有兩點補充，和 finding 的主張無關，只影響它的修法建議：
- 本機 store 的代號是 t00.TWSE／o00.TPEX，不是 TAIEX／TPEX_INDEX，修的時候要做代號對應。
- store 最新只到 9/17，比官方的 9/23 落後 4 個交易日。照建議改成直接讀 store，目前只會顯示 9/17 的 46,288.00／+0.96%，不會是 finding「expected」寫的 9/23 48,157.29／+0.75%。要顯示最新數字，還得讓 store 能及時更新，或另外處理資料過期。
    - 證據：
      - backend/app/taiwan/detail_service.py:597-600：`indices = self.index_provider.parse_tpex_rows([])` / `parse_taiex_rows([])`（寫死的空陣列）
      - backend/app/taiwan/enrichment/index.py:71、:131：`for r in rows:` 傳入空陣列必然回傳 []；backend/app/taiwan/detail_service.py:619-626 無條件回傳 status=unavailable
      - frontend/src/pages/TaiwanStockDetail.tsx:442-446：close 與 change_pct 為 null 時顯示 '--'；:358 的 `data && !isLoading` 讓卡片在所有個股頁都會顯示
      - git log -L585,626 只有 fd79b63 一個 commit；git diff HEAD 顯示這兩個檔案沒有未提交的修改；docs/ 查不到 parse_taiex_rows、market_context 或「大盤基準」的記錄
      - 指令：cd backend && uv run python ...\scratchpad\audit\work\verify-mktctx\repro.py（exit=0）；輸出在 ...\verify-mktctx\repro_out.json。app_market_context_TWSE 與 TPEX 都是 close=null、change=null、change_pct=null、meta.status=unavailable、source=index_provider；parse_taiex_rows([]) 與 parse_tpex_rows([]) 的長度都是 0
      - 同一個 provider 餵入官方原始資料（reference/raw/twse_taiex_mi_5mins_hist_202609.json）的結果：2026-09-23 close=48157.29、change=357.12、change_pct=0.75；官方 9/22 close=47800.17，自行計算 (48157.29-47800.17)/47800.17=0.7471%
      - 官方比對：reference/csv/twse_mi_index_20260923_indices.csv 第 3 行為「發行量加權股價指數,48157.29,+,357.12,0.75」；TPEx 為 reference/raw/tpex_index_inx_202609.json 的 9/23 列 413.76, -1.41，provider 算出 change_pct=-0.34
      - 並列比較：finding 寫的官方值 48,157.29／+0.75% → 我重算得 48,157.29／+357.12／+0.7471%，一致；finding 寫的 app 輸出 close=null、change_pct=null、unavailable → 我重現的 app 輸出相同
      - 本機 store：data/taiwan/index_daily 有 519 個分區、519 個 parquet。最新的 2026-09-17：t00.TWSE close=46288.0（前一日 45848.9，+439.10，+0.9577%），o00.TPEX close=398.17（-1.04，-0.2605%），和 reference/csv/index_daily_202608_202609.csv 第 35、73 行的官方 9/17 值一致
      - 執行前後 git status --porcelain 沒有差異，repo 沒有被寫入

### [P2 → P2] store 在當天 16:30 前後寫入的成交量／成交值，比官方最終值少（最多 −16.95%），且之後不會更新（確認成立）

- **主張**：研究脈絡的 price_context.volume／amount 取自 store；9/16 以後當天寫入的分區，大型股成交量低於證交所最終公布值，隔天才寫入的分區則完全一致。
- **正確的定義或數值**：store 的成交量與成交值應為官方最終值，或在最終值公布後重抓覆寫。
- **app 實際行為**：當天寫入的值偏低，且 21:40 補跑因分區已存在而不會更新。
- **對使用者的影響**：研究脈絡與 API 的成交量／值偏低，也會影響以成交量衍生的指標與大盤成交值；使用者自選的 2454、3034、2379、6196、3545 都有受影響的日子。
- **修法建議**：推測原因是 16:30～17:xx 時官方尚未計入鉅額等交易（差額皆為整千股）。建議 21:40 或隔日對最近 N 個分區重抓 MI_INDEX 並覆寫成交量與成交值。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/research_context.py:397-398
  - backend/app/taiwan/daily_refresh.py:167-176（已存在的分區不再更新）
- **線上證據**：
  - reference/csv/twse_stock_day_202608_202609.csv 與 reference/csv/twse_mi_index_20260923_quotes.csv（兩個官方來源在 9/23 一致，例：2330 為 22,817,873 股、56,893,530,280 元）
- **本地計算證據**：
  - store 與官方 STOCK_DAY 共 380 列，收盤價全部一致，成交量不一致 16 列，全部落在當天寫入的分區：9/16（16:30:22）、9/17（16:19:15）、9/18（17:03:37）、9/22、9/23。例：6196 在 9/17 為 808,428 對 973,428（−16.95%）；2330 在 9/18 為 37,190,511 對 40,892,688（−9.05%）；2330 在 9/23 為 21,978,873 對 22,817,873（−3.68%）。隔天早上寫入的 9/15、9/21 分區沒有差異
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：TaiwanDailyStore 在當天 16:19～17:03 寫入的分區，存的是 MI_INDEX 尚未定稿的成交量／成交值。9/16、9/17、9/18、9/22、9/23 皆偏低，最多 −16.95%（6196 在 9/17）。官方 STOCK_DAY、MI_INDEX 與鉅亨網的最終值三方一致，收盤價則全部相同。

之後不會更新，有兩道關卡：
1. 21:40 補跑時，daily_update.py:282-286 判定 store 已是最新，不呼叫 refresh_dates。
2. daily_refresh.py:167 的分區已存在就略過。

受影響的範圍：研究脈絡的 price_context.volume／amount 與 vol_ratio_5d、以成交量衍生的指標，以及大盤與產業的成交值。自選股中的 2454、3034、2379、6196、3545 都有受影響的日子。

更正：差額並非皆為整千股（9/18 的 2330、2454、3034 都不是）。9/18 缺少部分的均價恰好等於收盤價，原因不限於鉅額交易，確切機制仍屬推測。

同一個不重抓的關卡還造成 9/16、9/23 兩天的上櫃日K整批缺漏（9/23 TPEx 抓取失敗，狀態仍記為 success）。這不影響使用者的上市自選股。
    - 理由：這條 finding 的核心主張成立，嚴重度 P2 恰當。有兩處細節需要更正，另外發現一個原記錄沒提到的相關面向。

一、程式碼路徑（依現況行號）
1. 研究脈絡的 volume／amount 直接取自 TaiwanDailyStore 當天那一列（research_context.py:397-398），5 日量比也用同一批量（:420、:451-454）。
2. 排程每天 16:30 與 21:40 各跑一次（daily_pipeline.py:178-190、196-210）。
3. 盤後資料的切點是 16:00（daily_update.py:39-40、:114-117），所以 16:30 那班抓的是「當天」的 MI_INDEX。
4. 21:40 補跑時，第一道關卡在 daily_update.py:282-286：只要 store 最新日期已經 ≥ target，就直接記成「Daily store already current」，根本不會呼叫 refresh_dates。第二道關卡才是 finding 引用的 daily_refresh.py:150、167（分區已存在就略過）。
5. 實際 log 可以佐證：9/23 16:30:02 有抓 MI_INDEX（data/backend.log:37256）；21:40 那次（backend.log:45470-45478）只抓了股本資料，沒有任何 MI_INDEX 請求。

二、正確口徑（官方定義）
1. 官方 STOCK_DAY 與 MI_INDEX 的 notes 都寫明：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」。
2. app 解析的欄位就是 MI_INDEX 的「成交股數／成交金額」（snapshot_provider.py:164-165），口徑本身沒錯。問題在於抓取時官方數字還沒定稿，而且之後不再重抓。

三、數據核對
1. 我自己重跑了比對：store 與官方 STOCK_DAY 共 380 列，收盤價 0 列不一致；成交量／值有 16 列不一致，全部落在當天寫入的分區：
   - 9/16：寫入時間 16:30:22
   - 9/17：16:19:15
   - 9/18：17:03:37
   - 9/22：16:30:03
   - 9/23：16:30:05
2. 隔天早上才寫入的分區（9/15、9/21），以及 8 月由 9/16 回補寫入的分區，全部一致。
3. 另找了一個與 finding 不同的來源交叉核對：鉅亨網 charting API 的日量（單位為張，小數三位即股數）等於官方最終值，不等於 store 值。
   - 2330：9/18 為 40,892.688 張，9/23 為 22,817.873 張；9/15（15,215.752）與 9/21（16,086.51）則與 store 相同。
   - 6196：9/17 為 973.428 張，store 是 808,428 股（−16.95%）。

四、使用者情境
1. 自選股中受影響的日子：
   - 2454：9/16、17、18、22、23
   - 3034：9/17、18
   - 2379：9/18
   - 6196：9/17、18
   - 3545：9/18
2. 4 檔 ETF 在這段期間沒有差異。
3. 以目前的排程，穩定狀態下每個交易日都會發生：6 個當天寫入的分區中有 5 個出現差異，只有 9/21 是隔天早上才寫入所以沒事。
4. 其他下游消費者：大盤成交值加總 store 的 amount（market_intelligence.py:246），產業成交值也一樣（industry_intelligence.py:346、375）。

五、嚴重度
1. 口徑定義正確，但存下的是未定稿的初值，而且永遠不會被覆寫。多數差距在 0.1–9%，個別達 17%。
2. 這屬於明確的功能錯誤（P2）：不破壞資料結構，也不影響收盤價。
3. 修法可行：write_batch 合併時 keep="last"（daily_store.py:137），以 force 重抓就能覆寫。

六、需要更正的細節
1. fix_suggestion 說「差額皆為整千股」不成立。9/18 的差額 2330 −3,702,177、2454 −434,469、3034 −29,388 都不是整千股。
2. 9/18 那幾列差額換算的均價恰好等於當日收盤價（2454 為 4710、3034 為 558、2379 為 727、3545 為 51.6、6196 為 540）。這比較像盤後定價或零股，不只是鉅額交易。確切原因仍屬推測。
3. 我查不到官方「何時定稿」的公開說明：WebSearch 額度已用完，改用 WebFetch 查了幾個網站也沒找到。

七、新面向：上櫃資料整天缺漏（原記錄沒提到）
1. 9/16、9/23 兩個分區只有 1330 列上市股票，上櫃股票整批缺漏；其他日子上櫃約 1006-1009 列。
2. 9/23 16:30:05 的 log 顯示 TPEx 抓取失敗（incomplete chunked read），daily 狀態卻仍記為 success。21:40 因為同樣的「已是最新」關卡沒有重試，上櫃日K就這樣永久缺一天。
3. 使用者的自選全是上市股，不受影響；但選股器、大盤與產業統計會受影響。
    - 證據：
      - backend/app/taiwan/research_context.py:397-398（vol/amt 取自 store 當日列）、:420/:451-454（5 日量比用 store volume）
      - backend/app/taiwan/daily_update.py:282-286（d_max>=target 即 'Daily store already current'，21:40 不呼叫 refresh_dates；這是主要關卡）；:39-40、:114-117（16:00 切點，16:30 抓當日）
      - backend/app/taiwan/daily_refresh.py:150、167-170（分區已存在就略過，第二道關卡）
      - backend/app/jobs/daily_pipeline.py:178-190（16:30）、196-210（21:40，註解聲稱『已落盤的日期會被 refresh 略過』）
      - backend/app/taiwan/providers/snapshot_provider.py:129、164-165（MI_INDEX type=ALL，取『成交股數/成交金額』）
      - 官方口徑：reference/raw/twse_stock_day_2330_202609.json notes『當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。』；reference/raw/twse_mi_index_20260923.json 每日收盤行情 notes『本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。』
      - 本地重算：uv run python scratchpad/audit/work/verify-G3-vol/compare.py → joined 380、close mismatches 0、vol/amt mismatches 16；mismatch dates 9/16、9/17、9/18、9/22、9/23；part.parquet mtime 9/16 16:30:22、9/17 16:19:15、9/18 17:03:37、9/22 16:30:03、9/23 16:30:05；9/15 為 9/16 11:21:40、9/21 為 9/22 10:55:40（皆無差異）
      - 本地重算例：6196 9/17 官方 973,428 vs store 808,428（−16.95%）；2330 9/18 40,892,688 vs 37,190,511（−9.05%）；2330 9/23 22,817,873 vs 21,978,873（−3.68%）；2454 9/18 −434,469、3034 9/18 −29,388、2330 9/18 −3,702,177 非整千股；9/18 隱含價 2454=4710.0、3034=558.0、2379=727.0、3545=51.6、6196=540.0 皆等於收盤價
      - 線上獨立來源（鉅亨網）：https://ws.api.cnyes.com/ws/api/v1/charting/history?resolution=D&symbol=TWS:2330:STOCK&from=1790294400&to=1789084800 → v=[22817.873(9/23), 22009.927(9/22), 16086.51(9/21), 40892.688(9/18), 18116.974(9/17), 20919.285(9/16), 15215.752(9/15), ...]，等於官方最終值而非 store 值
      - 線上獨立來源（鉅亨網）：https://ws.api.cnyes.com/ws/api/v1/charting/history?resolution=D&symbol=TWS:6196:STOCK&from=1790294400&to=1789084800 → 9/17 v=973.428（張），9/18 v=2100.117
      - data/backend.log:37256（9/23 16:30:02 GET MI_INDEX）、:37258（16:30:05 TPEx snapshot fetch failed: incomplete chunked read）、:37265（daily=success）、:45470-45478（9/23 21:40 run 無 MI_INDEX 請求，daily=success）
      - 分區列數：9/16、9/23 只有 TWSE 1330 列，缺全部 TPEX；其他日 TPEX 1006-1009 列（polars 讀 data/taiwan/daily/date=*/part.parquet 分組計數）
      - 下游：backend/app/taiwan/market_intelligence.py:246（大盤成交值 = store amount 加總）；industry_intelligence.py:346、375
      - 修法可行性：backend/app/taiwan/daily_store.py:127-137（合併既有分區後 unique keep='last'，重抓即可覆寫）
      - 官方定稿時間：WebSearch 額度已用完，WebFetch 查過 Wikipedia 中／英文與 MoneyDJ 都沒有鉅額交易時段的說明，列為未查明
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：成立，數值完全重現。9/16 起，當天 16:19～17:03 抓 MI_INDEX 寫入的分區，個股成交量與成交值都比證交所最終公布值少；收盤價與開高低仍然正確。取 10 檔、380 列比對，共 16 列不一致，全部在 9/16、9/17、9/18、9/22、9/23 這 5 天。最大差距是 6196 在 9/17：808,428 對 973,428，少 16.95%。受影響的有 2330、2454、3034、2379、6196、3545，4 檔 ETF 都沒有差異。隔天才寫入的分區（9/15、9/21 與回補的 8/03～9/14）全部一致。

偏低的值之後不會被修正。21:40 那一班在 daily_update.py:282-286 就判定「store 已是最新」而直接跳過，連 refresh_dates 都沒呼叫；daily_refresh.py:167-170 是第二道跳過。偏低的成交量會一路傳到 kline_daily_enriched 與研究脈絡的 price_context。

有兩處要更正：
- 差額不是全部整千股，9/18 有 3 列不是整千。
- 17:03 抓的資料仍然偏低，所以重抓必須安排在更晚的時間，例如 21:40 或隔天，並對最近 N 個分區強制重抓後覆寫。

成因推測是盤後公布的交易（例如鉅額交易）尚未計入，但沒有外部證據，仍屬推測。

同一套「分區已存在就跳過」的機制還造成另一個問題：9/16 與 9/23 當天的 TPEx 抓取失敗，這兩天卻仍被記為成功，21:40 也不會補抓，所以這兩天的上櫃日線永久缺漏。使用者自選 9 檔全是上市，不受影響。
    - 理由：我自己寫腳本，把本地 store（data\taiwan\daily 的各日 part.parquet）和官方 STOCK_DAY 參考資料逐列比對，finding 的數字完全重現。兩邊共 380 列，全部對得上。收盤價與開高低不一致 0 列；成交量不一致 16 列，成交值不一致也是 16 列。這 16 列全部落在當天寫入的分區：9/16 有 2 列（分區 16:30:22 寫入）、9/17 有 4 列（16:19:15）、9/18 有 6 列（17:03:37）、9/22 有 2 列（16:30:03）、9/23 有 2 列（16:30:05）。隔天才寫入的 9/15（9/16 11:21:40 寫入）、9/21（9/22 10:55:40 寫入），以及 9/16 回補的 8/03～9/14，差異都是 0。

finding 舉的例子和我算的完全一樣：
- 6196 在 9/17：store 808,428，官方 973,428，差 −165,000（−16.95%）。
- 2330 在 9/18：store 37,190,511，官方 40,892,688（−9.05%）。
- 2330 在 9/23：store 21,978,873，官方 22,817,873（−3.68%）。

9/23 的 MI_INDEX 與 STOCK_DAY 兩份官方資料，10 檔的成交量與成交值全部一致，所以官方最終值本身可信。

「21:40 補跑不會更新」也成立，而且真正攔下的位置比 finding 引用的更早。daily_update.py:282-286 的 resolve_missing_date_range 在最新日期已經等於 target 時回傳 None，直接記成「Daily store already current」，根本不會呼叫 refresh_dates；daily_refresh.py:167-170 的 `if cur in available` 只是第二道關卡。log 可以證明：9/23 21:40:00 那一班從開始到結束（21:40:10）都沒有發出 MI_INDEX 請求。

受影響的只有個股：2330、2454、3034、2379、6196、3545。4 檔 ETF（0050、00878、00885、00893）都沒有差異。使用者自選的 2454、3034、2379、6196、3545 確實各有受影響的日子。成交量衍生的指標也受波及：kline_daily_enriched 在 9/17 的 6196 volume 也是 808,428。

需要修正的小地方有四點：
1. fix_suggestion 說「差額皆為整千股」不對。16 列中有 3 列不是整千，全部在 9/18：2330 差 −3,702,177、2454 差 −434,469、3034 差 −29,388。
2. 「16:30 前後」不夠精確。寫入時間在 16:19～17:03 之間，而且 17:03:37 寫入的 9/18 仍然偏低，所以重抓時間必須晚於 17:03，例如 21:40 或隔天。
3. 我另外算了缺漏部分的隱含成交價（差額成交值 ÷ 差額成交量），全部落在當日高低價區間內，例如 6196 在 9/17 為 516.0、2379 在 9/18 為 727.0。這和「盤後才公布的交易（例如鉅額交易）尚未計入」的推測相符，但我沒有查到官方的交易時段來佐證。WebSearch 額度已經用完，所以成因仍是推測。
4. 「大型股」一詞不準。3545 股價約 51 元，也受影響。

另外發現一個原記錄沒寫的新面向，根源是同一套「分區已存在就跳過」的邏輯：9/16 與 9/23 的分區只有 TWSE 1330 列，完全沒有 TPEx 的列。log 顯示 9/23 16:30:05 TPEx snapshot fetch failed（incomplete chunked read），整體狀態卻記成 success；21:40 那一班又因為日期已存在而跳過，所以這兩天的上櫃日線會永久缺漏。使用者自選 9 檔全是上市，不受影響。
    - 證據：
      - 比對腳本 AUDIT_SCRATCH/work/verify-volgap/compare.py；指令 `cd backend && uv run python compare.py`；輸出：official rows 380、store rows matched 380、close mismatches 0、open/high/low mismatches 0、volume mismatches 16、amount mismatches 16。明細在同目錄的 mismatches.csv
      - 逐日統計：9/16 2 列、9/17 4 列、9/18 6 列、9/22 2 列、9/23 2 列不一致；8/03～9/15 與 9/21 都是 0 列
      - 並列比較（finding 的數字對上我算的數字）：6196 在 9/17 為 808,428 對 973,428（−16.95%），我算出 −165,000（−16.95%），一致；2330 在 9/18 為 37,190,511 對 40,892,688（−9.05%），我算出 −3,702,177（−9.05%），一致；2330 在 9/23 為 21,978,873 對 22,817,873（−3.68%），我算出 −839,000（−3.68%），一致；2330 在 9/23 的成交值，store 54,802,017,935 對官方 56,893,530,280（−3.68%）
      - 差額不是整千股的 3 列（推翻 fix_suggestion 的「皆為整千股」）：2330 在 9/18 差 −3,702,177、2454 在 9/18 差 −434,469、3034 在 9/18 差 −29,388
      - 官方兩份來源的交叉比對：reference/csv/twse_mi_index_20260923_quotes.csv 與 twse_stock_day_202608_202609.csv，9/23 的 10 檔成交量與成交值全部相等（vol_eq 與 amt_eq 全為 true）
      - 分區寫入時間（PowerShell 查 Get-Item 的 CreationTime 與 LastWriteTime）：9/15 為 09-16 11:21:40、9/16 為 09-16 16:30:22、9/17 為 09-17 16:19:15、9/18 為 09-18 17:03:37、9/21 為 09-22 10:55:40、9/22 為 09-22 16:30:03、9/23 為 09-23 16:30:05
      - backend/app/taiwan/research_context.py:397-398：vol 與 amt 直接取自 daily_store 的 volume 與 amount 欄
      - backend/app/taiwan/daily_update.py:282-286：d_max >= target 時 resolve_missing_date_range 回傳 None，程式記下 'Daily store already current'，不會呼叫 refresh_dates
      - backend/app/taiwan/daily_refresh.py:167-170：`if cur in available:` 就記為 skipped，是第二道跳過
      - backend/app/jobs/daily_pipeline.py:194-198：21:40 班的註解寫明「已落盤的日期會被 refresh 略過, 不重複下載」
      - data/backend.log 第 37256 行：9/23 16:30:02 GET MI_INDEX?date=20260923；第 45470-45478 行：9/23 21:40:00～21:40:10 那一班沒有任何 MI_INDEX 請求
      - backend.log.1 第 48885-48886 行：9/18 17:03:32 抓 MI_INDEX?date=20260918，抓到的成交量仍然偏低
      - enriched 也受影響：data\kline_daily_enriched\date=2026-09-17\part.parquet 裡 6196.TWSE 的 volume 為 808428.0
      - 隱含成交價（差額成交值 ÷ 差額成交量）全部落在當日高低價區間內，例如 6196 在 9/17 為 516.0（區間 516～535）、2379 在 9/18 為 727.0（716～728）、2330 在 9/23 為 2492.86（2475～2505）；成因仍是推測，WebSearch 額度已用完，沒有取得鉅額交易時段的外部來源
      - 新面向：9/16 與 9/23 的分區只有 TWSE 1330 列，TPEx 0 列（其他日約 1006 列）。backend.log 第 37258 行為 '2026-09-23 16:30:05 TPEx snapshot fetch failed ... incomplete chunked read'，但第 37265 行記的是 daily=success

### [P3 → P3] market_rules_context.tick_size 沒有帶入 ETF 類別，0050、00878、00885、00893 都套用股票的升降單位（確認成立）

- **主張**：research_context 呼叫 TickSizeModel.get_tick_size(p_close) 時沒有傳 tick_class，一律視為普通股；同一檔 calc_limits 卻會帶入 ETF 類別，兩者不一致。
- **正確的定義或數值**：ETF 依 ETF 升降單位（未滿 50 元為 0.01，50 元以上為 0.05）。
- **app 實際行為**：4 檔 ETF 的 tick_size 是 5 倍或 10 倍的錯誤值。
- **對使用者的影響**：目前前端與 AI payload 都沒有使用 tick_size（只出現在 API 回應與 api.ts 型別），畫面上看不到；但 API 標示為 taiwan_market_rules 的 KNOWN 值卻是錯的，未來接上就會誤導。
- **修法建議**：改為 TickSizeModel.get_tick_size(p_close, tick_class=MarketProfileBridge.get_tick_size_class(inst))。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/research_context.py:731
  - backend/app/taiwan/market_rules.py:187-214（有 ETF 分支）
  - backend/app/taiwan/universe/models.py:118-121、172-182
- **線上證據**：
  - 交易所 MIS 官方五檔（reference/csv/mis_snapshot.csv，9/23）：0050 的買價 112.45/112.40/112.35/112.30/112.25（一檔 0.05）；00878 的買價 35.09/35.08/35.07/35.06/35.05（一檔 0.01）
- **本地計算證據**：
  - rc_audit_out.json（tick_check_0923）：0050 在 prev_close 111.85 時 app 給 0.5（ETF 類別應為 0.05）；00878 在 35.21 時給 0.05（應為 0.01）；00885 在 18.26 時給 0.05（應為 0.01）；00893 在 45.77 時給 0.05（應為 0.01）。5 檔股票皆正確
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：research_context.py:731 呼叫 TickSizeModel.get_tick_size(p_close) 時沒有傳入 tick_class，所以 market_rules_context.tick_size 對所有 ETF 都套用股票的升降單位。以 9/22 收盤為前一日收盤重算，app 給的值為：0050（111.85）0.5，正確是 0.05；00878（35.21）、00885（18.26）、00893（45.77）都是 0.05，正確是 0.01。交易所 MIS 五檔價差與 9/23 官方成交價都證明這些值不成立。在同一個 payload 裡，0050 與 00878 的 limit_up/limit_down 經由 calc_limits 帶入了 ETF 類別，而且與官方漲跌停相同，所以只有 tick_size 前後不一致；00885 與 00893 是國外成分 ETF，沒有漲跌停，不會呼叫 calc_limits，但 tick_size 一樣是錯的。目前前端只有型別宣告、AI payload 也沒有帶這個欄位，所以使用者看不到。
    - 理由：我用本地資料重算了一次，4 檔 ETF 的 tick_size 錯誤值與 finding 完全一致。

程式碼部分：backend/app/taiwan/research_context.py:731 寫的是 `tick_sz = TickSizeModel.get_tick_size(p_close)`，沒有傳入 tick_class，所以會落到預設的 TickSizeClass.ORDINARY_STOCK（market_rules.py:189-191）。同一段程式在第 730 行呼叫的 MarketProfileBridge.calc_limits，則會經由 universe/models.py:181 的 get_tick_size_class 帶入 ETF 類別（models.py:118-121：instrument_type=="etf" 時回傳 ETF）。market_rules.py:196-200 有 ETF 分支：未滿 50 元為 0.01，50 元以上為 0.05。

數據重現的做法：腳本以 load_cache 讀 data\taiwan\security_master.parquet，只讀取，不觸發 adapter 連網；再讀 daily\date=2026-09-22\part.parquet 取得前一日收盤（p_close）。這個值與官方 STOCK_DAY 的 9/22 收盤逐檔相符。app 算出的值與 finding 並列：
- 0050：p_close 111.85，app 給 0.5，帶入 ETF 類別是 0.05。finding 寫 0.5 對 0.05，一致。
- 00878：35.21，app 給 0.05，ETF 類別是 0.01，一致。
- 00885：18.26，app 給 0.05，ETF 類別是 0.01，一致。
- 00893：45.77，app 給 0.05，ETF 類別是 0.01，一致。
- 5 檔股票（2454/3034/2379/6196/3545）不論帶不帶類別，值都相同（5.0/1.0/1.0/1.0/0.1），一致。

用官方數據交叉驗證：
(1) MIS 五檔。0050 買價的相鄰價差只有 0.05，00878 只有 0.01。
(2) MIS 的漲跌停價。0050 跌停 100.7 不在 0.5 的格點上；00878 為 38.73/31.69。app 帶入 ETF 類別的 calc_limits 算出 (123.0, 100.7) 與 (38.73, 31.69)，與官方完全相同。
(3) 官方 9/23 的開高低收。以 app 的 tick 檢查，4 檔 ETF 都有價格落在格點外：0050 是 4 個價全部不在 0.5 格點上；00878 有 35.36/35.08/35.09；00885 有 18.26/18.24/18.27；00893 是 4 個價全部。以 ETF 類別的 tick 檢查則一個都沒有落在格點外。這證明 app 給的 tick 在交易所實際成交價上不可能成立。

影響面：前端只有 frontend/src/lib/api.ts:1327 的型別宣告，沒有任何畫面使用這個欄位。AI payload 在 backend/app/taiwan/ai_research.py:278-282 只帶 price_limit_pct 與 is_no_limit，不含 tick_size。所以目前使用者看不到，P3 合理。

小修正（不影響判定）：00885 與 00893 是國外成分 ETF，price_limit_pct 為 None，research_context 根本不會呼叫 calc_limits（第 729 行的條件）。因此「同一檔 calc_limits 會帶入 ETF 類別」這個對照，在 payload 內只對 0050、00878 成立；不過 tick_size 本身在 4 檔 ETF 上都是錯的。工作樹中這 3 個檔案相對 HEAD 沒有改動（git diff --stat 無輸出）。

未能執行：外部網站查詢 ETF 升降單位規則。WebSearch 回報本 session 的搜尋額度已用完（200/200），所以沒有外部網頁佐證；證據改以已下載的交易所官方 MIS 與 STOCK_DAY 實際報價為準。
    - 證據：
      - backend/app/taiwan/research_context.py:731 `tick_sz = TickSizeModel.get_tick_size(p_close)`：沒有傳入 tick_class；第 730 行的 calc_limits(p_close, inst) 則會依 instrument 決定類別
      - backend/app/taiwan/market_rules.py:189-200：tick_class 預設為 ORDINARY_STOCK；ETF 分支為未滿 50 元 0.01、50 元以上 0.05
      - backend/app/taiwan/universe/models.py:118-121 get_tick_size_class（etf→ETF），以及 :181 calc_limits 內呼叫 get_tick_size_class
      - 指令：cd backend && uv run python scratchpad\audit\work\verify-etftick\repro.py（exit 0；輸出存於 scratchpad\audit\work\verify-etftick\out.json）
      - 重算結果：0050 p_close=111.85 → app 0.5，ETF 類別 0.05；00878 35.21 → 0.05 對 0.01；00885 18.26 → 0.05 對 0.01；00893 45.77 → 0.05 對 0.01；股票 2454=5.0、3034=1.0、2379=1.0、6196=1.0、3545=0.1，帶不帶類別都相同
      - 官方 reference/csv/twse_stock_day_202608_202609.csv 的 9/22 收盤：0050 111.85、00878 35.21、00885 18.26、00893 45.77，與本地 daily parquet 相同
      - 官方 reference/csv/mis_snapshot.csv：0050 買價 112.45_112.40_112.35_112.30_112.25（價差 0.05），漲跌停 123.0/100.7；00878 買價 35.09_35.08_35.07_35.06_35.05（價差 0.01），漲跌停 38.73/31.69。app 帶入 ETF 類別的 calc_limits 算出 (123.0,100.7) 與 (38.73,31.69)，完全相同
      - 官方 9/23 開高低收中，落在 app tick 格點外的價格：0050 為 112.8/113.15/112.3/112.45（不在 0.5 格點）；00878 為 35.36/35.08/35.09；00885 為 18.26/18.24/18.27；00893 為 45.86/46.37/45.86/46.19（不在 0.05 格點）。以 ETF 類別 tick 檢查，落在格點外的為 0 筆
      - security_master.parquet：0050/00878 為 instrument_type=etf、domestic_equity；00885/00893 為 etf、foreign_equity、underlying_scope=foreign（所以 limit_pct=None，research_context 不會對這兩檔呼叫 calc_limits）
      - frontend/src/lib/api.ts:1327 只有型別宣告；backend/app/taiwan/ai_research.py:278-282 的 AI payload 只帶 price_limit_pct 與 is_no_limit，不含 tick_size
      - 外部網站佐證未能執行：WebSearch 回報 session 搜尋額度已用完（200/200）

### [P3 → P3] high_20d／low_20d 以收盤價計算卻沒有標註，AI 會把「距 20 日高點 0%」誤讀成股價位於最高點（確認成立）

- **主張**：20 日高低取的是 20 個收盤價的極值，而常見的「N 日最高／最低」（如 Donchian 通道）取最高價／最低價；欄位與 AI payload 都沒有說明口徑。
- **正確的定義或數值**：欄位名稱或 formula 註明 close-based；或改用最高價與最低價。
- **app 實際行為**：沒有標註，AI 拿到 distance_from_20d_high＝0 時，可能描述為「位於 20 日高點」，但當日盤中價曾高出 5% 以上。
- **對使用者的影響**：AI 報告與 API 使用者可能誤解位階；研究卡本身沒有顯示這兩個欄位。
- **修法建議**：在 EvidenceMeta.formula 或欄位名稱標示 close-based（例：high_20d_close），並在 AI payload 附上口徑說明。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/research_context.py:432-438
  - backend/app/taiwan/ai_research.py:185-186
  - backend/app/taiwan/research_context.py:136-139（註解只寫 decimal）
- **線上證據**：
  - https://en.wikipedia.org/wiki/Donchian_channel（引文：Upper band "HighestHigh(N)"，Lower band "LowestLow(N)"，N 通常為 20）
- **本地計算證據**：
  - 9/23 的 2454：app 的 distance_from_20d_high＝0（close 5185＝收盤 20 日高點），以官方最高價算 20 日高點為 5480，距離 −5.38%；6196 為 0 對 −1.61%；0050 為 0 對 −1.14%；low 端 2454 為 +34.15% 對 +35.73%（rc_audit_out.json）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：high_20d／low_20d 取的是含當日在內、最近 20 個收盤價的極值。常見的 N 日最高、最低（例如 Donchian 通道，N 通常為 20）取的是最高價與最低價，但 EvidenceMeta 沒有 formula，欄位名稱、AI payload 與 SYSTEM_PROMPT 也都沒有說明口徑，個股 AI 與比較頁 AI 都受影響。以 9/23 為例，自選 9 檔中有 5 檔的 distance_from_20d_high 為 0：2454、2379、6196、0050、00893。改用最高價口徑則分別是 −5.38%、−4.16%、−1.61%、−1.14%、−0.39%。2454 的低點端也不同：收盤價口徑 +34.15%，最低價口徑 +35.73%。措辭需修正：2454 高出收盤 5.69% 的 5480 是 9/22 的盤中最高價，不是 9/23 當日；9/23 當日最高 5265，只比收盤高 1.54%。研究卡與比較頁的 UI 都沒有顯示這兩個欄位，影響只在 AI 報告與 API 使用者，維持 P3。
    - 理由：程式碼、本地 parquet、官方 STOCK_DAY 參考資料三方都對得上，finding 的核心說法與全部數值都重現了，判定 confirmed。

1. 程式碼：high_20d／low_20d 取的是 20 個收盤價的極值。hist_df 的 close 去掉 null 後取最後 20 筆，含目標日。PriceContextEvidence 的 meta 沒有 formula 欄位，只有 classification、source、as_of。欄位註解只寫 decimal。AI payload 只放兩個 distance 欄位，SYSTEM_PROMPT 也沒有說明口徑。比較頁的 AI 也沿用同一個 build_evidence_registry，所以一樣受影響。前端只在 api.ts 宣告型別，研究卡與比較頁都只顯示 return_5d、return_20d，沒有顯示這兩個欄位。

2. 數值重現：腳本 verify.py 用 polars 逐檔讀本地 parquet，重現 app 的算法。9/23 的 20 日窗為 2026-08-27 到 2026-09-23，9 檔都與官方交易日完全一致。
   - 2454：app 算出距 20 日高點 0.0000%（收盤 5185 等於 20 日收盤最高 5185）；以官方最高價算，20 日最高是 5480，距離 −5.3832%。finding 寫 −5.38%，一致。
   - 2454 低點端：app 為 +34.1527%（20 日收盤最低 3865）；以最低價算是 +35.7330%（20 日最低 3820）。finding 寫 34.15% 對 35.73%，一致。
   - 6196：0 對 −1.6129%（20 日收盤最高 549，最高價 558），與 finding 的 −1.61% 一致。
   - 0050：0 對 −1.1429%（20 日收盤最高 112.45，最高價 113.75），與 finding 的 −1.14% 一致。
   - 本地 parquet 算出的最高價、最低價極值與官方完全相同，所以差異只來自口徑，不是資料錯誤。

3. finding 沒提到的補充：自選 9 檔在 9/23 共 5 檔出現距 20 日高點等於 0，比 finding 列的 3 檔多了 2379（最高價口徑 −4.1562%）和 00893（−0.3882%）。收盤價口徑因為窗內含當日收盤，只要當日收盤是 20 日收盤最高就會等於 0，所以比最高價口徑容易出現 0。

4. 線上來源：Wikipedia 的 Donchian channel 條目寫明上軌為 "HighestHigh(N)"、下軌為 "LowestLow(N)"，"N is usually equal to 20"，並寫 "formed by taking the highest high and the lowest low of the last n periods"。Investopedia 抓取失敗（工具無法存取），不作為證據。

5. 措辭需要小修正：finding 的 actual 寫「當日盤中價曾高出 5% 以上」不精確。2454 在 9/23 當日的最高價是 5265，只比收盤高 1.54%。高出 5% 以上的 5480 發生在 9/22，是 20 日窗內的盤中最高價，比收盤高 5.69%。這不影響 finding 的核心結論。

6. 嚴重度：收盤價口徑本身是可接受的定義，問題在沒有標註。UI 不顯示這兩個欄位，影響只在 AI 報告與 API 使用者，維持 P3。

我只寫入工作目錄 scratchpad\audit\work\verify-hl20\，沒有修改 repo，也沒有連線證交所或櫃買中心。
    - 證據：
      - backend/app/taiwan/research_context.py:419 closes = hist_df["close"].drop_nulls().to_list()；:432-438 window_20_closes = closes[-20:]，h_20d=max、l_20d=min，dist=c_price/h_20d-1
      - backend/app/taiwan/research_context.py:486-490 PriceContextEvidence 的 meta=EvidenceMeta(classification, source="taiwan_daily_store", as_of)，沒有 formula；:136-139 欄位註解只寫 decimal
      - backend/app/taiwan/ai_research.py:179-192 payload 只放 distance_from_20d_high/low，沒有口徑說明；:318-353 SYSTEM_PROMPT 沒有定義 20 日高低
      - backend/app/taiwan/comparison_ai_research.py:42 import build_evidence_registry（比較頁的 AI 也拿到相同欄位）
      - frontend/src/lib/api.ts:1257-1260 只宣告型別；TaiwanStockDetail.tsx:881-888 與 TaiwanStockCompare.tsx:339-355 只顯示 close、return_5d、return_20d
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-hl20/verify.py（輸出 verify_out.json）
      - 2454.TWSE 9/23：close=5185；收盤 20 日最高 5185，距離 0.0000%；官方最高價 20 日最高 5480，距離 −5.3832%；收盤 20 日最低 3865，距離 +34.1527%；最低價 20 日最低 3820，距離 +35.7330%。窗為 2026-08-27..2026-09-23，與官方交易日一致
      - 6196.TWSE：收盤 20 日最高 549、最高價 20 日最高 558 → 0.0000% 對 −1.6129%；0050.TWSE：112.45、113.75 → 0.0000% 對 −1.1429%
      - finding 未列：2379.TWSE 0.0000% 對 −4.1562%（761 對 794）；00893.TWSE 0.0000% 對 −0.3882%（46.19 對 46.37）；3034 −2.3297% 對 −3.8801%；3545 −2.2857% 對 −3.9326%；00878 −0.3408% 對 −1.2662%；00885 −2.9740% 對 −4.1448%
      - 官方 twse_stock_day_202608_202609.csv：2454 在 2026-09-22 的最高價 5480、2026-09-23 的最高價 5265、2026-08-31 的最低價 3820、2026-08-27 的收盤 3865
      - https://en.wikipedia.org/wiki/Donchian_channel：Upper band "HighestHigh ( N )"、Lower band "LowestLow ( N )"；"N is usually equal to 20"；"formed by taking the highest high and the lowest low of the last n periods"
      - https://www.investopedia.com/terms/d/donchianchannels.asp：WebFetch 無法存取，未採用

### [P3 → P3] 融資資料尚未公布時（16:30～21:40），AI 證據整段丟掉大盤與產業脈絡，但畫面仍顯示（確認成立）

- **主張**：ai_research 只在 status 屬於 (current, complete) 時放入 market／industry context，而兩者的 overall_status 要日線、法人、融資都 current 才是 complete。融資在 21:40 才更新，前段時間都是 partial，於是被當成缺失。
- **正確的定義或數值**：漲跌家數、成交值與產業統計只依賴日線，應以日線狀態決定是否放入 AI 證據。
- **app 實際行為**：每個交易日 16:30～21:40 產生的 AI 報告，會說大盤與產業資料不足，與同一頁研究卡的顯示矛盾。
- **對使用者的影響**：這段時間手動產生的 AI 報告缺少大盤與產業解讀。
- **修法建議**：閘門改看 daily 的 section 狀態，或接受 partial 並把狀態一併放進 payload。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/ai_research.py:140-158、161-176
  - backend/app/taiwan/market_intelligence.py:576-582
  - backend/app/taiwan/industry_intelligence.py:249-255
  - backend/app/jobs/daily_pipeline.py:180、198
  - frontend/src/pages/TaiwanStockDetail.tsx:905-916（研究卡不看 status，照樣顯示產業成交佔比與 5D RS）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 模擬融資 store 缺 9/23：ctx 的 daily 為 current；market／industry 為 partial；ctx 仍有 advance_count 538、產業 turnover_share 0.3956、RS5 0.0354；但 payload 內沒有 market_context 與 industry_context，missing 列出 market_context、industry_context（rc_audit2_out.json 的 ai_registry_margin_not_yet_published）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：每個交易日 16:30 日線與法人寫入後，到 21:40 融資寫入完成前，目標日已經是當天（16:00 起就切換），但融資 store 還只到前一日（stale）。於是大盤與產業 snapshot 的 overall_status 是 partial，ai_research.py:140、161 的閘門只放行 complete，把只依賴日線的 market_context（advance_ratio、上下漲家數、成交值）和 industry_context（成交佔比、產業上漲比例、5 日與 20 日相對強弱）從 AI 證據中移除，並列進 missing。

實測 2026-09-23：上漲家數 538、半導體業佔比 0.395583、5 日相對強弱 0.035416 都已算好，兩個情境的數值完全相同，但融資未公布時 payload 就是不含這兩段。

同樣的問題也發生在多檔比較的 AI 報告（comparison_ai_research.py:173 重用同一個函式）。

研究卡標題會顯示「(狀態: 部分)」，只有產業列不看狀態照樣顯示數值；研究卡本來就不顯示大盤漲跌家數，所以「和研究卡矛盾」只適用於產業。

21:40 若融資抓取失敗，這個狀態可能延續到隔天 16:00（推測，未驗證重試機制）。
    - 理由：我用唯讀方式自己重現了一遍：store 以子類別包裝並停用所有寫入方法，security master 讀的是複本，網路 adapter 換成呼叫就報錯的替身。目標日固定為 2026-09-23，用本地 parquet 跑兩個情境：(A) 資料全數公布；(B) 融資 store 只到 9/22，模擬融資還沒公布。

先確認時間窗：resolve_target_latest_trading_date 在 9/23 15:59 回傳 9/22，16:00、18:00、21:39 都回傳 9/23。所以 16:00 過後目標日就變成當天，但融資要等 21:40 那班排程才寫入。

情境 B 下，自選 5 檔個股（2454、3034、2379、6196、3545）的 section 狀態都是：日線 current、法人 current、融資 stale，大盤 partial，產業 partial。build_evidence_registry 產生的 payload 沒有 market_context，也沒有 industry_context；missing 清單多出 market_context、industry_context（另有 margin_context，那是正確的）。0050 也少了 market_context。情境 A 則兩段都在。

finding 的數字全部重現：ctx 的上漲家數 538、半導體業成交佔比 0.395583（finding 寫 0.3956）、5 日相對強弱 0.035416（finding 寫 0.0354）。我另外用 polars 直接讀 parquet 重算，不經過 app 程式碼：上漲 538、下跌 647、平盤 137，半導體業佔比 0.395583，其他電子業 0.049411，都與 app 一致。

兩個情境的 market 與 industry 數值完全相同，差別只在 payload 有沒有放進去。這證明 payload 用到的欄位只依賴日線：market 是 advance_ratio、advance_count、decline_count、market_turnover；industry 是 turnover_share、advance_ratio、relative_strength_5d、relative_strength_20d。finding 的 expected 成立。

閘門有一個細節：它接受 ("current", "complete")，但 overall_status 的型別只有 complete、partial、unavailable 三種，所以實際上只有 complete 能通過。

需要補充修正的地方：
(1) 研究卡標題會顯示「(狀態: 部分)」（TaiwanStockDetail.tsx:870），所以研究卡並非完全不看狀態。只有產業列（905-916）不看 section 狀態，照樣顯示數值。另外研究卡本來就不顯示大盤漲跌家數，所以「和研究卡矛盾」只適用於產業，不適用於大盤。
(2) 原 finding 沒提到：多檔比較的 AI 報告也受影響。comparison_ai_research.py:173 對每檔標的重用 build_evidence_registry，目標日同樣由 comparison.py:209、216 解析。
(3) 時間窗應該描述成「16:30 日線、法人寫入成功之後，到 21:40 融資寫入完成之前」。16:00～16:30 之間日線本身也還沒寫入，那是另一種情況。21:40 若融資抓取失敗，狀態可能一直 partial 到隔天 16:00 目標日切換為止。這點是推測，我沒有驗證重試機制。

嚴重度維持 P3：報告少了大盤與產業的解讀，但沒有產生錯誤數值。

執行前後，data\taiwan 沒有任何比 06:30 新的檔案，git status 與基準相同。
    - 證據：
      - 重現腳本：AUDIT_SCRATCH/work/verify-rcgate/repro.py；輸出：同目錄 repro_out.json；獨立重算：同目錄 indep.py
      - 指令：cd backend && uv run python ...\verify-rcgate\repro.py（exit 0）。解析出的目標日：{15:59→2026-09-22, 16:00→2026-09-23, 18:00→2026-09-23, 21:39→2026-09-23}
      - 情境 B（融資只到 9/22），2454.TWSE：sections={daily: current, institutional: current, margin: stale, market: partial, industry: partial}；payload 有 market_context＝False、有 industry_context＝False；missing=['fundamentals','market_context','industry_context','margin_context','fundamentals_context']。3034、2379、6196、3545 結果相同；0050 同樣缺 market_context
      - 情境 A（全數公布）：market 與 industry 都是 complete，payload 兩段皆在：market={advance_ratio 0.407, advance_count 538, decline_count 647, market_turnover 887250849184.0}；半導體業 industry={turnover_share 0.395583, advance_ratio 0.4271, rs5 0.035416, rs20 0.033647}
      - 數字並列：上漲家數 finding 538／app 重現 538／polars 獨立重算 538（下跌 647、平盤 137）；半導體業成交佔比 finding 0.3956／重現 0.395583／獨立重算 0.395583；5 日相對強弱 finding 0.0354／重現 0.035416。所有 ctx 數值在兩個情境間完全相同（indep.py 印出 True）
      - backend/app/taiwan/ai_research.py:140 閘門 `if ctx.market_context.status in ("current", "complete")`；:161 產業閘門同樣寫法；不符合時分別在 :157、:175 把該段加入 missing_items
      - backend/app/taiwan/market_intelligence.py:179 overall_status 型別為 Literal[complete, partial, unavailable]；:576-582 要三者都 current 才是 complete，所以 'current' 這個分支永遠不會成立；industry_intelligence.py:127、:249-255 相同
      - backend/app/taiwan/research_context.py:641、:666：market 與 industry evidence 的 status 直接取自兩個 snapshot 的 overall_status
      - backend/app/taiwan/daily_update.py:39-40、:112-117 截止時間為 16:00，過了就以當天為目標日；daily_pipeline.py:180-189 的 16:30 排程 refresh_margin=False，:194-203 的 21:40 排程才抓融資
      - frontend/src/pages/TaiwanStockDetail.tsx:870 研究卡標題顯示「(狀態: 部分)」；:905-916 產業列只檢查 industry 是否存在，不看 status
      - 新面向：backend/app/taiwan/comparison_ai_research.py:173 對每檔標的呼叫 build_evidence_registry；comparison.py:209、:216 用同一個目標日解析規則，所以多檔比較的 AI 報告也會丟掉大盤與產業脈絡
      - 前端呼叫 ai-research 時沒帶日期（TaiwanStockDetail.tsx:140；api.ts:2952-2959 送出 date: null），因此都走預設目標日解析
      - 寫入檢查：執行後 `find data/taiwan -newermt '2026-09-24 06:30'` 沒有結果；git status 仍是原本那 3 個 M 檔

### [P3 → P3] 研究卡的證據計數與狀態標籤是寫死的；monitor_context 與 realtime_context 也標為 KNOWN 但不是實際資料（確認成立）

- **主張**：known_cnt 基數固定為 10，derived_cnt 固定為 12，不隨實際缺值變動；上櫃 9/23 價格 MISSING 時 overall 仍為 complete；前端把 unavailable 也顯示成「部分」。monitor_context 寫死 0 條規則且標為 KNOWN；realtime_context 固定為 market_closed，last_price 用日線收盤。
- **正確的定義或數值**：計數依實際非 null 欄位計算；overall 應反映價格缺失；前端應區分 unavailable；監控與即時欄位不應以 KNOWN 呈現寫死值。
- **app 實際行為**：畫面顯示「已認證事實 17 項｜衍生計算 12 項（狀態：完整）」，但價格欄其實全缺。
- **對使用者的影響**：研究卡的品質標示不可信（使用者目前 0 條規則，monitor 寫死值剛好正確）。
- **修法建議**：依欄位實際值計數；overall 納入 price_context 的 classification；monitor 讀規則庫或標 MISSING。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/research_context.py:783-796
  - backend/app/taiwan/research_context.py:747-757
  - backend/app/taiwan/research_context.py:767-773
  - frontend/src/pages/TaiwanStockDetail.tsx:870、874
- **線上證據**：
  - （無）
- **本地計算證據**：
  - rc_audit2_out.json（evidence_summary）：2454 在 9/24（價格 MISSING、return_20d null）時 derived＝12、known＝10；6488.TPEX 在 9/23（價格 MISSING）時 known＝17、derived＝12、overall＝complete
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：研究卡的證據計數和狀態標籤不反映該標的的實際缺值。

1. derived_fields_count 是常數 12。known_fields_count 以 10 為基數，這 10 項固定把 OHLCV 算成已知；只有在法人 1 日值、融資、ETF 存在時才分別加 3、4、4。所以價格全缺時這兩個計數都不會減少。

2. overall_status 只用全域分區日期判斷 daily／inst／margin 是否 current，不檢查該標的當日有沒有資料列。實測 6488.TPEX 在 2026-09-23：本地 daily 分區完全沒有上櫃資料，但官方當日收盤為 950、成交 28,714,838 股。此時 price_context 為 MISSING，研究卡卻顯示「狀態: 完整｜已認證事實 17 項｜衍生計算 12 項」。2454.TWSE 在 9/24（16:00 後、資料尚未寫入）顯示「部分｜10 項｜12 項」，但實際衍生欄位只有約 7 項非 null。

3. 前端把 partial 和 unavailable 都顯示成「部分」。

4. monitor_context 寫死 0 條規則、0 則告警並標為 KNOWN，沒有讀取既有的規則庫。

5. realtime_context 的 status 寫死為 market_closed，last_price 是日線收盤而不是即時報價。收盤值本身是真實資料，缺價時分類也會正確變成 MISSING。

6. realtime_context 與 monitor_context 目前沒有在前端顯示，ai_research 與 comparison 也不使用，影響僅限 API 使用者。

7. 另外，缺當日價時技術指標實際只算到前一交易日，as_of 卻標成目標日。
    - 理由：我先把 daily、institutional、margin 三個 store 和 security_master.parquet 複製到 scratch，再把服務的各個 store 指向這份複本，並封鎖網路，然後直接呼叫 TaiwanStockResearchContextService.get_research_context 重現。另外用 polars 直接讀 parquet，並對照官方參考資料做獨立檢查。finding 裡的數字全部重現。

並列比較（finding → 我的結果）：
- 2454.TWSE 在 9/24：price MISSING → MISSING；return_20d null → null；known 10 → 10；derived 12 → 12。實際上非 null 的欄位只有：價格衍生 3/9、技術面 4/8，但 derived 仍報 12。
- 6488.TPEX 在 9/23：price MISSING → MISSING（價格原始欄 0/6）；known 17 → 17；derived 12 → 12；overall complete → complete，前端標籤是「完整」。

缺值根因：本地 daily 的 2026-09-23 分區只有 1330 筆，全部是上市（TPEX 為 0 筆）。9/22 的分區則有 1009 筆上櫃。官方 TPEx 9/23 行情顯示 6488 當天有正常交易：收盤 950、成交 28,714,838 股。所以價格缺失是本地資料缺口，不是停牌。overall 會判成 complete，是因為 daily_status 只看全域分區日期存不存在（research_context.py:376-381），不看該標的有沒有當日列。

前端 TaiwanStockDetail.tsx:870 只要不是 'complete' 就一律顯示「部分」，所以 unavailable 也會顯示成「部分」，確認屬實。不過 unavailable 只在三個 store 都沒有 ≤ target 的分區時才會出現，實務上很少見。

monitor_context（753-757）寫死 0／0 並標為 KNOWN，確認屬實。repo 裡其實有 app/api/monitor_rules.py 規則庫可以讀，但這裡沒讀。

realtime_context 的 status 寫死為 market_closed（748），last_price 用日線收盤，確認屬實。有兩點需要修正 finding 的措辭：
1. last_price 是真實的日線收盤值；價格缺失時分類會正確變成 MISSING（6488 在 9/23、2454 在 9/24 都是）。所以「不是實際資料」的說法過重，真正的問題是狀態固定、不是即時報價。
2. known_cnt 並不完全固定：法人 1 日值存在時加 3，融資存在時加 4，ETF 再加 4。它不會變動的，是 OHLCV 有沒有缺。

影響範圍：realtime_context 與 monitor_context 在前端只出現在型別定義（frontend/src/lib/api.ts:1330-1337），畫面沒有顯示。ai_research.py 與 comparison.py 也都不讀它們，所以只影響 API 使用者。

對使用者的影響：自選 9 檔都是上市股，9/23 有資料，所以上櫃缺口只在查看上櫃個股時才會碰到。9/24 這種情況則會落在每日 16:00 截止之後、資料寫入之前的空檔，自選股也會遇到：研究卡顯示「部分」、衍生計算 12 項，但價格全缺。

另外發現一點新情況：6488 在 9/23 的技術面 MA／RSI 實際上是用到 9/22 為止的收盤算的，卻標示 as_of 為 9/23。

嚴重度維持 P3（屬於品質標示與局部體驗問題）。已確認 repo 的 data 目錄沒有任何被寫入的檔案（find -newer 為 0），git status 也和開始時相同。
    - 證據：
      - backend/app/taiwan/research_context.py:783-788 known_cnt = 10（註解寫 identity, OHLCV, 1D flows），只在 f_1d／mb／ETF 存在時才遞增；derived_cnt = 12 是常數
      - backend/app/taiwan/research_context.py:376-381 daily_status 由全域分區日期 available_dates() 決定，不檢查該標的當日有沒有資料列；767-773 overall 只看 daily／inst／margin 這三個狀態
      - backend/app/taiwan/research_context.py:747-757 realtime 的 status 寫死為 "market_closed"、last_price=c_price；monitor 的 active_rule_count=0、recent_alert_count=0，分類為 KNOWN
      - frontend/src/pages/TaiwanStockDetail.tsx:870 overall_status === 'complete' ? '完整' : '部分'；874 直接顯示 known_fields_count／derived_fields_count
      - 前端 grep：realtime_context／monitor_context 只出現在 frontend/src/lib/api.ts:1330-1337 的型別定義，頁面沒有顯示；backend 的 ai_research.py、comparison.py 也都不讀取
      - 服務重現（scratchpad\audit\work\verify-g3rc-evsum\svc_run.py → svc_run_out.json，指向資料複本並封鎖網路）：2454.TWSE 在 2026-09-24 → price_meta=MISSING、close=null、return_20d=null、known=10、derived=12、overall=partial（前端顯示「部分」）；非 null 欄位為價格衍生 3/9、技術面 4/8
      - 服務重現：6488.TPEX 在 2026-09-23 → price_meta=MISSING、價格原始欄 0/6、技術面 4/8、inst_1d 3/3、margin 4/4、known=17、derived=12、overall=complete（前端顯示「完整」）；realtime.last_price=null、分類為 MISSING；monitor 為 0/0、KNOWN
      - 對照組：6488.TPEX 在 9/22 與 2454.TWSE 在 9/23，全部欄位非 null，known=17、derived=12、complete；00878.TWSE 在 9/23 的 known=21（ETF +4）
      - polars 直接讀 parquet（raw_check.py → raw_check_out.json）：data/taiwan/daily/date=2026-09-23 共 1330 筆，全部為 TWSE、TPEX 為 0；9/22 有 TPEX 1009 筆；institutional 9/23 的 6488.TPEX foreign_net=-6082314，margin 9/23 的 6488.TPEX margin_balance=15551000，兩者都存在
      - 官方參考 reference\csv\tpex_daily_quotes_20260923.csv：6488 環球晶 2026-09-23 close_twd=950.0、volume_shares=28714838、amount_twd=27580042174；tpex_trading_stock_202608_202609.csv 的 9/23 同樣是 close 950.0、28715 張，表示當天正常交易，本地缺的是資料
      - 唯讀確認：find data -newer marker_before 共 0 個檔案；git status 仍只有開工前就存在的 3 個 M

### [P3 → P3] parse_taiex_rows 把第一列的昨收設為開盤價，月初第一天的漲跌錯誤（確認成立）

- **主張**：series 第一列沒有前一日資料時，prev_close＝open_idx，漲跌變成「收盤減開盤」。
- **正確的定義或數值**：第一列的昨收應取上個月最後一筆收盤；取不到時設為 None。
- **app 實際行為**：每月第一個交易日的漲跌與漲跌幅錯誤。
- **對使用者的影響**：目前正式程式只以空陣列呼叫此函式（見 P2 大盤卡 finding），屬潛在錯誤；修大盤卡時若直接餵當月資料，就會顯示錯誤的月初漲跌。
- **修法建議**：第一列 prev_close 設為 None（change 為 None），或由呼叫端傳入前一日收盤。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enrichment/index.py:85-90、113
- **線上證據**：
  - 官方 MI_5MINS_HIST：8/31 收盤 46,128.47，9/01 收盤 46,948.72（reference/raw/twse_taiex_mi_5mins_hist_202608.json、_202609.json）
- **本地計算證據**：
  - 把官方 9 月資料餵入 parse_taiex_rows：9/01 的 app change 為 771.61（1.67%），以 8/31 收盤計應為 820.25（1.78%）；第二列（9/02）−784.0 正確（rc_audit2_out.json 的 parse_taiex_first_row）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：parse_taiex_rows 在第一列沒有前一日資料時，把 previous_close 設成當日開盤價，所以漲跌變成「收盤減開盤」。用官方資料重算：9/01 的 app 為 +771.61（1.67%），官方口徑以 8/31 收盤 46128.47 計應為 +820.25（1.78%）；8/03 也同樣錯。第二列起全部正確。只輸入單列時錯誤一樣會發生：9/23 的 app 為 +262.52（0.55%），官方 MI_INDEX 為 +357.12（0.75%）。目前正式程式只以空陣列呼叫（detail_service.py:598-600），屬於潛在錯誤；現有測試也沒有驗證 change。
    - 理由：我用官方參考資料自己重算了一次，finding 的數字完全重現。把 reference/raw/twse_taiex_mi_5mins_hist_202609.json 轉成 openapi 格式（ROC 日期與 OpeningIndex、HighestIndex、LowestIndex、ClosingIndex 欄位），再餵給 TaiwanIndexProvider().parse_taiex_rows，並與官方數字並列比對。

- 9/01（第一列）：app 的 previous_close＝46177.11，就是當日開盤；change 771.61、change_pct 1.67%。以官方 8/31 收盤 46128.47 計算，應為 820.25、1.78%。與 finding 所列數字一字不差。
- 9/02 起：app 為 −784.0（−1.67%），與預期相同。第 2 列到第 17 列全部吻合（rows_2_to_n_all_match=true）。
- 8 月資料同樣如此：8/03 的 app 漲跌是收盤減開盤，為 605.99（1.42%）。第 2 列到第 21 列全部正確。
- 本地 parquet 可以交叉驗證：data/taiwan/index_daily/date=2026-08-31 的 t00.TWSE 收盤為 46128.47，date=2026-09-01 的開盤 46177.11、收盤 46948.72，都與官方一致。

另外補了兩點 finding 沒提的證據：
- 官方 MI_INDEX 9/23 的加權指數漲跌是 +357.12（0.75%），等於 48157.29 − 47800.17（9/22 收盤）。可見官方口徑是對前一日收盤，不是對開盤。
- 只餵單列（9/23）時，app 得出 262.52（0.55%），同樣錯在第一列。所以只要輸入只有一列，例如來源只回當日資料，錯誤就會出現在唯一一列上。這一點由程式邏輯推得；openapi 端點實際回傳幾列，我沒有連線去查。

影響範圍：正式程式只在 detail_service.py:598-600 以空陣列 [] 呼叫這個函式，函式直接回傳空 list，所以目前不會把錯誤數值顯示給使用者，P3 合理。現有測試 test_taiex_parsing 只驗證 open 和 close，沒有驗 change 與 previous_close，因此抓不到這個問題。
    - 證據：
      - backend/app/taiwan/enrichment/index.py:86-90：prev_close = open_idx；只在 results 非空時才改為 results[-1].close；change = close_idx - prev_close
      - backend/app/taiwan/enrichment/index.py:113：previous_close=prev_close（第一列會是開盤價）
      - backend/app/taiwan/detail_service.py:598-600：正式程式只以 parse_taiex_rows([])／parse_tpex_rows([]) 呼叫，回傳空 list，目前沒有實際影響
      - backend/tests/test_taiwan_data_enrichment.py:368-386：test_taiex_parsing 只有單列，也只驗 open／close，沒有驗 change／previous_close
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-taiex1st/repro.py（輸出存於同目錄 out.json，exit=0）
      - 重算結果 9/01：app 為 prev_close 46177.11、change 771.61、1.67%；以官方 8/31 收盤 46128.47（raw/twse_taiex_mi_5mins_hist_202608.json 最後一筆）計算應為 820.25、1.78%，與 finding 相同
      - 重算結果 9/02：app 與預期都是 −784.0（−1.67%）；9 月第 2～17 列、8 月第 2～21 列全部相符
      - 8/03（8 月第一列）：app 為 605.99（1.42%），等於收盤 43386.41 減開盤 42780.42，同樣是收盤減開盤
      - 單列輸入（只給 9/23）：app 的 prev_close 為 47894.77（當日開盤），change 262.52（0.55%）
      - 官方 MI_INDEX（reference/csv/twse_mi_index_20260923_indices.csv 第 3 行）：發行量加權股價指數 48157.29，漲跌 +357.12，0.75%；357.12＝48157.29−47800.17（9/22 收盤），證明官方漲跌以前一日收盤為基準
      - 本地 parquet：data/taiwan/index_daily/date=2026-08-31\part.parquet 中 t00.TWSE 收盤 46128.47；date=2026-09-01 開盤 46177.11、收盤 46948.72，與官方一致

### [P3 → P3] 比較頁把所有 null 一律顯示為「N/A（不適用）」，個股本益比與缺資料的收盤價都被說成「不適用」（確認成立）

- **主張**：fmtPct／fmtNum 對 null 固定輸出「N/A（不適用）」；個股的 fundamentals 永遠是 unavailable（缺資料，不是不適用），價格缺失時也同樣顯示。
- **正確的定義或數值**：缺資料顯示「資料缺失」，只有結構上不適用（例如 ETF 的 PE）才顯示「不適用」。
- **app 實際行為**：缺資料被標成不適用。
- **對使用者的影響**：使用者會誤以為個股沒有本益比這個概念，或誤以為該檔當日不適用比較。
- **修法建議**：依 meta.classification 或 status 區分 MISSING 與 not_applicable 的顯示文字。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/pages/TaiwanStockCompare.tsx:135-136、377-385
  - backend/app/taiwan/research_context.py:685-697（個股 fundamentals 恆為 unavailable）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 6488.TPEX 在 9/23 的 close 為 null，會顯示「N/A（不適用）」；自選 5 檔個股的 PE 皆為 null（fundamentals status＝unavailable）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：比較頁的 fmtPct 和 fmtNum 遇到 null，一律顯示「N/A（不適用）」（TaiwanStockCompare.tsx:135-136）；外資買賣超 1 日欄位也寫死同一個字樣（:371-373）。後端對所有個股都寫死 fundamentals status="unavailable"（research_context.py:685-686），而 pe 是 None，所以個股的 PE 一定會顯示「N/A（不適用）」。自選的 5 檔個股都會這樣；4 檔 ETF 則正確顯示「ETF 不適用」。

價格缺資料時也會被標成「不適用」。以 6488.TPEX 在 9/23 為例，官方收盤價是 950.0，但本地 9/23 分區完全沒有 TPEX 列（0 列），並不是 close 欄位為 null，後端因此回傳 close=null，畫面就顯示成「N/A（不適用）」。9/23 另有 7 檔上市標的的 close 真的是 null，顯示結果相同。自選 9 檔在 9/23 的收盤價都和官方一致，不會出現這種情況。

後端已經用 meta.classification='MISSING' 標示缺資料，前端卻沒有拿來區分「資料缺失」和「不適用」。另外，同一列中 RSI14 仍會顯示用舊日期資料算出的數值，例如 6488 是 46.20，畫面沒有提示這個值不是比較日當天的。
    - 理由：我用程式碼追蹤加本地資料重算，重現了這條 finding。在 TaiwanStockCompare.tsx:135-136，fmtPct 和 fmtNum 遇到 null 時一律輸出「N/A（不適用）」。PE 欄位在 :381-383 只有 status==='not_applicable' 時才顯示「ETF 不適用」，其他情況都交給 fmtNum 處理。後端 research_context.py:685-686 對 instrument_type=='stock' 直接寫死 fund_status="unavailable"，而 pe 預設值是 None（research_context.py:197）。所以個股的 PE 一定會顯示成「N/A（不適用）」。

後端其實有區分「不適用」和「缺資料」：價格缺失時，meta.classification 是 "MISSING"（research_context.py:487）。前端型別 api.ts:1194 也有這個欄位，但比較頁完全沒用到。

本地重算的方法：自己組 65 日視窗，照後端公式算出 9/23 的每一格。

- 自選 5 檔個股（2454、3034、2379、6196、3545）的 PE 格都是「N/A（不適用）」。
- 4 檔 ETF（0050、00878、00885、00893）正確顯示「ETF 不適用」。
- 自選 9 檔在 9/23 的收盤價都和官方參考資料一致，例如 2454 是 5185.0、0050 是 112.45，所以使用者的自選股不會碰到收盤價顯示成「不適用」。
- 6488.TPEX 在 9/23 的收盤價格確實顯示「N/A（不適用）」，官方 TPEx 9/23 收盤價則是 950.0。

需要補正一個細節：原因不是 parquet 裡該列的 close 是 null，而是 date=2026-09-23 分區只有 TWSE 1330 列、TPEX 0 列，9/16 也一樣，整個上櫃資料段都缺。後端因此得到 c_price=None。另外，9/23 分區有 7 檔上市標的的 close 真的是 null（1538、6952、7732、00625K、00643K、00682U、00707R），比較時同樣會顯示「不適用」。

另有兩個原記錄沒提到的面向：
1. :371-373 的外資買賣超 1 日欄位，遇到 null 也寫死「N/A（不適用）」。
2. 6488 在 9/23 的收盤價、5 日和 20 日報酬率都顯示「不適用」，但 RSI14 仍顯示 46.20。這個值是用截至 9/22 的收盤價算的，因為 closes 在 drop_nulls 之後只到 9/22，畫面上卻沒有提示它比比較日舊。

嚴重度維持 P3，因為這是局部顯示用語，不影響數值和計算。另外，finding 指出的已知問題清單（docs/code-review-2026-09-22.md）裡沒有這一項。
    - 證據：
      - frontend/src/pages/TaiwanStockCompare.tsx:135-136：fmtPct/fmtNum 在 v == null 時回傳 'N/A（不適用）'
      - frontend/src/pages/TaiwanStockCompare.tsx:381-383：只有 status==='not_applicable' 顯示 'ETF 不適用'，否則 fmtNum(pe)
      - frontend/src/pages/TaiwanStockCompare.tsx:371-373：外資買賣超 1 日的 null 也寫死 'N/A（不適用）'（finding 未提到）
      - backend/app/taiwan/research_context.py:685-686：stock 的 fund_status 寫死為 "unavailable"；:194-197 的 pe 預設為 None
      - backend/app/taiwan/research_context.py:487：close 缺失時 meta.classification='MISSING'；前端型別 frontend/src/lib/api.ts:1194 有這個欄位，但比較頁沒用到
      - 本地重算（指令：cd backend && uv run python ...\scratchpad\audit\work\verify-cmpna\repro.py）：2454/3034/2379/6196/3545 的 fund.status=unavailable，PE 格是 'N/A（不適用）'；0050/00878/00885/00893 的 PE 格是 'ETF 不適用'
      - 同一個指令算出自選 9 檔 9/23 的本地收盤價：2454=5185.0、3034=545.0、2379=761.0、6196=549.0、3545=51.3、0050=112.45、00878=35.09、00885=18.27、00893=46.19，和官方 csv/twse_stock_day_202608_202609.csv 的 9/23 close_twd 逐一相同
      - 6488.TPEX：row@9/23=False，close=None，收盤格='N/A（不適用）'，ret5=None，ret20=None，但 RSI14=46.2（用截至 9/22 的資料算出）；官方 csv/tpex_daily_quotes_20260923.csv 的 6488 環球晶 close_twd=950.0
      - tpex_cov.py 的輸出：date=2026-09-23 分區 twse=1330、tpex=0（9/16 也是 tpex=0；9/22 是 tpex=1009）；9/23 的 null-close 標的有 00625K、00643K、00682U、00707R、1538、6952、7732.TWSE

### [P2 → P2] 目標日不在 store 時（每日 16:00～16:30，及平日國定假日），研究脈絡的價格欄全缺，previous_close 與漲跌停價退回前兩日的收盤（部分成立）

- **主張**：預設目標日在 16:00 後就改成今天，但日線 16:30 才寫入。日曆也沒有任何假日資料，平日的國定假日會被當成交易日。這時 prev_date＝available_dates[-2]，實際是前兩個交易日；c_price 為 None；均線與 20 日高低改用前一交易日的資料，卻標為 as_of 目標日。
- **正確的定義或數值**：目標日應取「store 裡最新的已收盤交易日」，或以該日為準一致推算；previous_close 與漲跌停應以目標日前一個實際交易日的收盤為基準；假日不應成為目標日。
- **app 實際行為**：每個交易日 16:00～16:30 都會出錯。以即將到來的中秋為例，從 9/25 16:00 到 9/29 16:00 約 4 天，研究卡的 5／20 日報酬與乖離都顯示 —；比較頁預設模式全部顯示「N/A（不適用）」並跳出「超出本機資料範圍」；AI 報告的價格證據全為 null。API 回傳的 previous_close 與漲跌停也都偏一天。
- **對使用者的影響**：即將到來的連假期間，研究卡、比較頁與 AI 報告都拿不到任何價格資料；平日 16:00～16:30 打開詳細頁同樣如此。
- **修法建議**：研究脈絡與比較頁的預設目標日改用 min(resolve_target, store 最新日)；prev_date 改取嚴格小於目標日的最後一個 store 日期；日曆補入官方休市日。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/daily_update.py:39-40、110-119（16:00 cutoff；is_trading_day 回 None 時不會後退）
  - backend/app/taiwan/realtime/calendar.py:65-79、164（預設日曆沒有 known_holidays）
  - backend/app/api/taiwan.py:352（TaiwanStockResearchContextService() 使用空日曆）
  - backend/app/jobs/daily_pipeline.py:180（日線 16:30 才更新）
  - backend/app/taiwan/research_context.py:376-400、441-448、486-490、502-506、728-731
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/%E4%B8%AD%E7%A7%8B%E8%8A%82（引文：「中秋節——2026年9月25日」；「臺灣將中秋節列為國定假日，農曆八月十五日休假一天」）
- **本地計算證據**：
  - 模擬 target=2026-09-24（store 最新為 9/23）：2454 的 previous_close 為 5180（9/22 收盤），應為 5185；漲跌停 5695/4665，以 PriceLimitModel 依 5185 計算應為 5700/4670。6196 為 588/481.5，應為 603/494.5。0050 為 123.0/100.7，應為 123.65/101.25。價格欄全部 MISSING、return_20d 為 null，ma5／ma20／high_20d 其實是 9/23 的值卻標為 as_of 2026-09-24（rc_audit_out.json 的 target_not_in_store_0924）
  - resolve_target_latest_trading_date 實跑：9/25 16:10 得 9/25；9/26 10:00 得 9/25；9/28 10:00 得 9/25；9/28 16:10 得 9/28；9/29 10:00 得 9/28
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：研究脈絡不帶日期時，預設目標日由 resolve_target_latest_trading_date 決定：16:00 以後就是今天，而且預設日曆沒有任何休市日，平日休市會被當成交易日。日線要等 16:30 那班排程才寫入，休市日則永遠沒有分區。以下兩個時段，目標日都不在 store 裡：
- 每個交易日 16:00 到該班寫入完成（失敗時延到 21:40 或更晚）；
- 連假期間，例如 9/25 16:00 到 9/29 16:30 以後。9/25 中秋與 9/28 教師節都休市。

這時研究脈絡會出現以下錯誤：
- close／open／high／low 為 null，價格區標為 MISSING；
- return_5d、return_20d、MA20 乖離、5 日量比為 null；
- ma5、ma20、ma60、rsi14、high_20d、low_20d 用的是前一交易日的資料，卻標成 as_of 目標日；
- previous_close 取 available_dates[-2]，也就是前兩個交易日的收盤，漲跌停因此跟著偏一天。以 target=9/24 為例，程式輸出的正好是官方 9/23 的漲跌停：2454 輸出 5695/4665，應為 5700/4670。

實際受影響的畫面是個股詳細頁的研究卡（5 日報酬、20 日報酬、MA20 乖離顯示「—」），以及從詳細頁手動觸發的 AI 報告（價格證據為 null，漲跌停偏一天）。比較頁不受影響：它的預設模式以 data-status 的 daily_as_of（store 最新日）當日期，只有 data-status 請求失敗時才會退回有問題的預設值。
    - 理由：後端機制、數值偏差、詳細頁與 AI 報告的影響都已由我獨立重現，成立。但 finding 說比較頁也受影響，這點不成立。

1) 預設目標日：resolve_target_latest_trading_date 在 16:00 以後就把目標日設為今天（daily_update.py:39、115-116）。日線只有排程會寫入：16:30 那班，失敗時 21:40 再跑一次（daily_pipeline.py:149、180）。除此之外，只有 CLI（daily_update.py:458）會呼叫 run_update，沒有其他地方在 16:30 前寫入當日日線。

2) 日曆：TaiwanTradingCalendar 預設不含任何休市日（calendar.py:51-57）。遇到平日，is_trading_day 回 None（calendar.py:79）。後退迴圈只在結果為 False 時才往前推（daily_update.py:110、118）。app 內沒有任何地方呼叫 add_holiday。研究脈絡每次請求都建立新的空日曆（api/taiwan.py:352 → research_context.py:308）。實跑結果：9/25 16:10 得 9/25，9/28 10:00 得 9/25，9/28 16:10 得 9/28，9/29 10:00 得 9/28，而 is_trading_day(9/25) 與 is_trading_day(9/28) 都是 None。

3) 休市日會不會進 store：daily_refresh.py:174-179 遇到空結果時不寫分區。實查 store，2025-09-29、10-06、10-10、10-24、12-25 這幾個平日休市日都沒有分區，所以休市日永遠不會成為 store 裡的日期。

4) research_context.py 的算法：available_dates 只取 ≤ target 的日期（376）。prev_date 取 available_dates[-2]（390）。目標日不在 store 時，[-1] 是前一個交易日，[-2] 就變成前兩個交易日。curr_row 為空，所以 c_price 為 None（393）。return_5d、return_20d、dist_ma20、vol_ratio 都依賴 c_price 或 vol，全部變成 null（424、428、445、451）。ma5／ma20／ma60／rsi／high_20d／low_20d 仍用前一日的資料計算，但 as_of 標成目標日（489、506）。漲跌停依 p_close 計算（728-731）。

5) 我自己用 target=9/24 實跑，store 最新日為 9/23，全程唯讀，資料目錄沒有任何檔案變動：
- 2454：previous_close 5180，漲跌停 5695/4665。以 9/23 收盤 5185 計算應為 5700/4670。
- 6196：588/481.5，應為 603/494.5。
- 0050：123.0/100.7，應為 123.65/101.25。
- ma5、ma20、high_20d 與 target=9/23 的結果逐位相同，tech_as_of 卻是 2026-09-24。
以上與 finding 的數字完全一致。

6) 正確規則：官方 MIS 快照（本地參考資料）顯示，交易日 20260923 的 2454 為 y=5180（9/22 收盤）、u=5695、w=4665；6196 為 u=588、w=481.5；0050 為 u=123.0、w=100.7。可見 D 日的漲跌停是以前一交易日收盤為基準。程式對「9/24」輸出的數字，正好就是官方 9/23 的漲跌停。
休市日方面，我用了與 finding 不同的來源：開源交易所日曆 exchange_calendars 的 XTAI（台灣證交所）定義。其中 TeachersDay 從 2025 年起列為 9/28 休市，中秋日期表列 2026 年為 2026-09-25。因此 9/25（五）與 9/28（一）都休市，連同週末共 4 天。

7) 使用者影響：
- 詳細頁會受影響。TaiwanStockDetail.tsx:124-129 不帶日期呼叫 research-context，:140 的 AI 報告也不帶日期，對應 ai_research.py:381。研究卡的 5 日報酬、20 日報酬、MA20 乖離在 881-894 行會顯示「—」。
- 比較頁不會受影響。TaiwanStockCompare.tsx:63-69 的預設模式以 /data-status 的 daily_as_of 為日期，該值是 store 的最新日（daily_update.py:199）。前端註解也明寫這樣做是為了「避免開啟即一片 N/A」。所以只有 data-status 請求失敗時，比較頁才會退回無日期模式。finding 說比較頁「全部顯示 N/A（不適用）並跳出超出本機資料範圍」，並不成立。
- previous_close 與漲跌停這兩欄不在詳細頁顯示，只影響 API 回應與 AI 證據。

8) 時間窗細節：
- 連假期間出錯的時段應為 9/25 16:00 到 9/29 16:30 以後（要等 9/29 的日線寫入），不是 finding 寫的到 9/29 16:00。
- 平日的出錯時段是 16:00 到 16:30 那班寫入完成為止。若那班失敗，或後端當時沒開、超過 misfire 寬限，會延到 21:40 或更晚。

9) 重複檢查：這個問題不在 code-review-2026-09-22.md 已記錄的項目裡。

10) 嚴重度：P2 恰當。這是明確的功能錯誤，會週期性發生，而且即將到來的連假就會碰到。錯誤的漲跌停雖然屬於金融口徑錯誤，但畫面上不顯示，只出現在 API 和手動觸發的 AI 證據，不足以升到 P1。
    - 證據：
      - backend/app/taiwan/daily_update.py:39、110、115-118：cutoff 為 16:00；後退迴圈只在 is_trading_day 為 False 時才往前推，遇到 None 不後退
      - backend/app/taiwan/realtime/calendar.py:51-57、65-79：known_holidays 預設為空，平日回 None；grep 全 app，沒有任何地方呼叫 add_holiday（service.py:131 只呼叫 add_trading_day）
      - backend/app/jobs/daily_pipeline.py:149、180、198：run_update 只由 16:30 與 21:40 兩班排程呼叫（另有 CLI daily_update.py:458）
      - backend/app/taiwan/daily_refresh.py:174-179：抓到空結果時不寫分區；實查 store，2025-09-29、10-06、10-10、10-24、12-25 都沒有分區
      - backend/app/taiwan/research_context.py:356、376、390、393、424-454、489、506、728-731：target 預設值、prev_date=available_dates[-2]、c_price 為 None 時各欄轉為 null、as_of 標成 target
      - frontend/src/pages/TaiwanStockDetail.tsx:124-129、140、881-894：研究脈絡與 AI 報告都不帶日期；5 日報酬、20 日報酬與 MA20 乖離為 null 時顯示「—」
      - frontend/src/pages/TaiwanStockCompare.tsx:63-69、147-163：比較頁預設以 data-status 的 daily_as_of（store 最新日，daily_update.py:199）作為日期，所以不會觸發此問題（反駁 finding 的比較頁主張）
      - 本地實跑 AUDIT_SCRATCH/work/verify_g3_rc_target/verify.py（結果在同目錄 out.json）：target=2026-09-24 時，2454 previous_close=5180、limit 5695/4665，以 9/23 收盤計算應為 5700/4670；6196 為 588/481.5，應為 603/494.5；0050 為 123.0/100.7，應為 123.65/101.25；close、return_5d、return_20d、distance_to_ma20 全為 null；ma5=4917.0 與 target=9/23 相同，tech_as_of=2026-09-24；data_dir_changed_files=[]
      - resolve_target 實跑：9/24 15:59→9/23、9/24 16:10→9/24、9/25 16:10→9/25、9/26 10:00→9/25、9/28 10:00→9/25、9/28 16:10→9/28、9/29 10:00→9/28、9/29 16:10→9/29；is_trading_day(9/25) 與 is_trading_day(9/28) 均為 None
      - 官方 MIS 快照（reference\csv\mis_snapshot.csv，date_d=20260923）：2454 的 y=5180.0、u=5695.0、w=4665.0；6196 的 u=588.0、w=481.5；0050 的 u=123.0、w=100.7。D 日漲跌停以前一交易日收盤為基準，程式對 9/24 輸出的正是 9/23 的官方值
      - https://raw.githubusercontent.com/gerrymanoim/exchange_calendars/master/exchange_calendars/exchange_calendar_xtai.py 原文：TeachersDay = Holiday("Teachers' Day", month=9, day=28, start_date="2025", observance=nearest_workday)；中秋節列在 adhoc_holidays
      - https://raw.githubusercontent.com/gerrymanoim/exchange_calendars/master/exchange_calendars/lunisolar_holidays.py 的 mid_autumn_festival_dates：2026 年為 "2026-09-25"
      - docs/code-review-2026-09-22.md：已查過，未記錄研究脈絡目標日或休市日相關問題，不是重複回報
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：預設目標日的判定在台北時間 16:00 就切到當天，但日線要到 16:30 排程才寫入（實測約 16:30:05；後端沒在跑時會拖到下次補抓）。預設日曆也沒有任何休市日，所以平日的國定假日（2026-09-25 中秋、09-28 教師節）會被當成目標日。

只要目標日不在 store，研究脈絡的 prev_date 就是 available_dates[-2]，實際是前兩個交易日。後果如下：
- previous_close、漲跌停價與 tick 都以前兩日的收盤計算。實測 2454 為 5180 與 5695/4665，應為 5185 與 5700/4670；6196 為 588/481.5，應為 603/494.5；0050 為 123.0/100.7，應為 123.65/101.25。
- close/OHLCV、change、return_5d/20d 與各項距離都是 null，price meta 為 MISSING。
- ma5/20/60、rsi14、high/low_20d 其實是前一交易日的值，但 price 與 technical 的 meta.as_of 都標為目標日。只有 data_quality 的 daily 項目標出 stale（as_of 為前一交易日）。

受影響的時段：每個交易日 16:00 到日線寫入為止，以及連假期間 9/25 16:00 至 9/29 約 16:30。這段時間比較頁預設模式會顯示 N/A 與「超出本機資料範圍」。

AI 報告並非「價格證據全為 null」：收盤與報酬類欄位是 null，previous_close、均線、RSI 與 20 日高低點則是陳舊或錯位的數值。
    - 理由：我在隔離環境裡重跑了真正的 TaiwanStockResearchContextService：先把 data/taiwan 複製到 scratchpad，再讓各 store 指向這份複本。自選 9 檔分別用 target=2026-09-23（目標日在 store 內）與 target=2026-09-24（store 最新只到 9/23）各跑一次，finding 列的數值全部逐一重現。

1. 2454：target=9/24 時 previous_close 為 5180（9/22 收盤），漲跌停 5695/4665。官方 9/23 收盤是 5185，我用自己寫的 tick 規則計算，應為 5700/4670。
2. 6196：588/481.5，應為 603/494.5。
3. 0050：123.0/100.7，應為 123.65/101.25。
4. 其餘幾檔也同樣退回前兩日的收盤。3034 的 prev 為 557，應為 545；2379 為 755，應為 761。
5. 9 檔的 close、return_5d、return_20d 都是 None，price meta 為 MISSING。
6. ma5、ma20、high_20d 與 target=9/23 那次完全相同，也跟我用官方 9/23 收盤算出的值一致。例如 2454 的 ma5 4917.0、ma20 4531.25、h20 5185.0。但 price_context 與 technical_context 的 meta.as_of 都標為 2026-09-24。
7. 用 target=9/23 跑時，prev 與漲跌停都正確，而且跟官方參考資料一致。可見偏差只來自「目標日不在 store」這條路徑。

原因在程式碼：available_dates 取的是 <= target 的日期（research_context.py:376），prev_date 固定取 available_dates[-2]（:390）。所以目標日缺席時，prev 會退回兩個交易日前。

時間窗口的部分：resolve_target_latest_trading_date 實跑結果如下。

- 9/24 15:59 得 9/23；9/24 16:00 得 9/24。
- 9/25 16:10 得 9/25；9/26 10:00 與 9/28 10:00 都得 9/25。
- 9/28 16:10 得 9/28；9/29 10:00 得 9/28。

這些與 finding 相符。原因是 is_trading_day 對平日回傳 None，而程式只在回傳 False 時才往前退。全 repo 也沒有任何地方呼叫 add_holiday。

日線寫入時間：9/22 與 9/23 分區的 mtime 分別是 16:30:03 與 16:30:05，證實 16:00～16:30 的窗口確實存在。9/21 的分區直到 9/22 10:55 才寫入，表示後端沒在跑或排程失敗時，窗口會拉得更長。

假日方面，2026-09-25（週五）是中秋節，已由 Wikipedia 核實。2026-09-28（週一）是教師節；英文 Wikipedia 記載 2025/5/9 已三讀恢復 9/28 為國定假日，2026-09-28 是週一由本地計算確認。所以連假期間的目標日會落在 9/25、9/28 這兩個休市日。

需要修正的細節：

- finding 說「AI 報告的價格證據全為 null」言過其實。為 null 的是 close/OHLCV、change、return_5d/20d 與各項距離；previous_close、high_20d、low_20d、ma5/20/60、rsi14 仍有值，只是屬於前一交易日，或是退回前兩日。
- data_quality.sections 的 daily 項目其實有標出 stale、as_of 2026-09-23，陳舊狀態並非完全沒揭露。
- 連假窗口應為 9/25 16:00 至 9/29 約 16:30，不是 16:00。

以上都不影響核心結論。這是限於時段的明確功能錯誤，維持 P2。
    - 證據：
      - backend/app/taiwan/research_context.py:376 available_dates 取 <= target 的日期；:390 prev_date = available_dates[-2]；:393 c_price 在目標日缺資料時為 None；:489、:506、:742 的 meta as_of=str(target)；:730 漲跌停以 p_close 計算
      - backend/app/taiwan/daily_update.py:39 cutoff 為 16 點；:110、:118 只在 is_trading_day(...) is False 時往前退；backend/app/taiwan/realtime/calendar.py:79 平日回傳 None；:164 預設日曆沒有 holiday；repo 內只有 realtime/service.py:131 呼叫 add_trading_day，沒有任何地方呼叫 add_holiday
      - backend/app/jobs/daily_pipeline.py:180 日線排程為 mon-fri 16:30；backend/app/api/taiwan.py:352 使用預設建構子（空日曆）
      - 腳本 AUDIT_SCRATCH/work/verify-rctarget/run_rc.py（store 指向 data_copy），輸出在同目錄的 out_rc.json
      - target=2026-09-24 實跑：2454 prev=5180 lim=5695/4665（依官方 9/23 收盤 5185 應為 5700/4670）；6196 prev=535 lim=588/481.5（依 549 應為 603/494.5）；0050 prev=111.85 lim=123.0/100.7（依 112.45 應為 123.65/101.25）；3034 prev=557（應 545）；2379 prev=755（應 761）；9 檔 close、r5、r20 全為 None，price meta 為 MISSING，as_of 為 2026-09-24
      - 同一次實跑：2454 ma5=4917.0、ma20=4531.25、h20=5185.0，與 target=9/23 的結果相同；以官方 reference csv 重算 9/23 的值也得 4917.0/4531.25/5185.0（6196 為 535.0/516.05/549.0，0050 為 110.71/108.4125/112.45），但 tech meta as_of=2026-09-24；data_quality 的 daily 項目為 ('daily','stale','2026-09-23')
      - target=2026-09-23 對照組：9 檔的 prev_close 都等於官方 9/22 收盤，漲跌停與我獨立計算的結果一致（例如 2454 為 5695/4665）
      - resolve_target 實跑：9/24 15:59 得 9/23；9/24 16:00 得 9/24；9/25 16:10 得 9/25；9/26 10:00 得 9/25；9/28 10:00 得 9/25；9/28 16:10 得 9/28；9/29 10:00 得 9/28
      - 分區 mtime：daily date=2026-09-22 為 2026-09-22 16:30:03、date=2026-09-23 為 2026-09-23 16:30:05；date=2026-09-21 為 2026-09-22 10:55:40（當晚未寫入）
      - https://zh.wikipedia.org/zh-tw/%E4%B8%AD%E7%A7%8B%E8%8A%82 原文：「中秋節——2026年9月25日」、「臺灣將中秋節列為國定假日，農曆八月十五日休假一天」
      - https://en.wikipedia.org/wiki/Public_holidays_in_Taiwan：9/28 孔子誕辰於 2016 年取消放假，「On May 9, 2025, the Legislative Yuan passed the third reading」後恢復為國定假日；本地計算確認 2026-09-25 為週五、2026-09-28 為週一
      - frontend/src/pages/TaiwanStockCompare.tsx:135-136 null 值顯示「N/A（不適用）」；:159-162 comparison_date > daily_as_of 時顯示「超出本機資料範圍」
      - 執行前後，真實 data/taiwan 在 10 分鐘內沒有任何檔案被修改；git status 與基準相同

## 確認正確的項目

- **price_context.return_20d（以 closes[-21] 為基準，research_context.py:426-429）**：用官方 STOCK_DAY 獨立計算 close_T/close_{T-20 交易日}-1，對照 app 實際輸出（隔離資料副本跑 TaiwanStockResearchContextService.get_research_context），範圍是自選 9 檔 × 9/1～9/23 共 153 組，最大差異 0。例：2454 在 9/23 為 5185/3945-1＝31.4322%，基準日 8/26。第三方：HiStock 的 3 日 +10.1%、一週 +14.5%、兩週 +12.1%，與 3／5／10 個交易日前為基準的算法一致（我算出 10.08%、14.46%、12.11%）。HiStock 的「一個月 +37.7%」以日曆月計（基準 8/24 收盤 3765，算出 37.72%），口徑不同，不是錯誤
  - 來源：AUDIT_SCRATCH/work/G03gap/rc_audit.py
  - 來源：AUDIT_SCRATCH/work/G03gap/rc_audit_out.json
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
  - 來源：https://histock.tw/stock/2454（原文：三日 +10.1%、一週 +14.5%、兩週 +12.1%、一個月 +37.7%，收盤 5185，2026/09/23）
- **high_20d／low_20d 的數值（以收盤價計、含當日，research_context.py:432-434）**：依 app 的收盤價定義獨立重算 153 組，差異 0。例：2454 在 9/23 的 high_20d＝5185、low_20d＝3865。定義爭議另列為 P3 finding
  - 來源：rc_audit_out.json（app_vs_indep）
- **distance_from_20d_high／distance_from_20d_low（research_context.py:435-438）**：用 close/high20-1 與 close/low20-1 獨立重算 153 組，差異 0
  - 來源：rc_audit_out.json
- **technical_context.ma5／ma20（research_context.py:441-446）**：依簡單移動平均的定義（最近 N 個收盤價的算術平均，含當日），以官方收盤價重算 153 組，差異 0。例：2454 在 9/23 的 MA5＝4917、MA20＝4531.25
  - 來源：https://en.wikipedia.org/wiki/Moving_average（引文：SMA 為 "the unweighted mean of the previous k data-points"，SMA_k=(p_{n-k+1}+…+p_n)/k）
  - 來源：rc_audit_out.json
- **technical_context.ma60（research_context.py:447-448）**：9 檔 × 9/22、9/23 共 18 組，以 store 收盤價重算，與 app 一致（例：2454 在 9/23 為 4067.1667，窗口 7/1～9/23）。store 在 8～9 月的收盤價經比對，與官方 STOCK_DAY 380 列全部相同；6～7 月沒有官方參考資料，未核對（見 unverifiable）
  - 來源：rc_audit_out.json（ma60_check）
- **technical_context.distance_to_ma20（乖離率，research_context.py:445-446）與研究卡顯示（TaiwanStockDetail.tsx:891-895，×100 取 2 位）**：公式 close/MA20-1 與維基百科的 BIAS 定義相同。153 組重算差異 0。例：2454 在 9/23 為 14.43%
  - 來源：https://zh.wikipedia.org/zh-tw/%E4%B9%96%E9%9B%A2%E7%8E%87（引文：nBIAS = (Close − MAₙ) / MAₙ × 100%）
- **above_ma5／above_ma20（research_context.py:498-499）**：以官方收盤價重算 close>MA，153 組全部一致
  - 來源：rc_audit_out.json（沒有任何 mismatch 計數）
- **previous_close／change／change_pct（目標日在 store 時，research_context.py:389-402）**：153 組與官方前一交易日收盤一致，差異 0
  - 來源：rc_audit_out.json
- **market_rules_context.limit_up／limit_down／price_limit_pct（目標日在 store 時，research_context.py:718-731，MarketProfileBridge.calc_limits 會帶入 ETF 的升降單位類別，universe/models.py:172-182）**：9/23 的 app 值與交易所 MIS 官方快照逐檔相同：2454 為 5695/4665（y＝5180）、6196 為 588/481.5（y＝535）、0050 為 123.0/100.7（y＝111.85）、00878 為 38.73/31.69（y＝35.21）。00885、00893 屬國外成分 ETF，is_no_limit＝true，與 PriceLimitModel 一致（A05 已驗）
  - 來源：reference/csv/mis_snapshot.csv（u_limit_up、w_limit_down、y_prev_close_ref_price）
  - 來源：rc_audit_out.json（tick_check_0923）
- **closes／volumes 各自 drop_nulls 後，收盤序列是否錯位（自選 9 檔，research_context.py:419-420）**：最近 65 個 store 分區（6/24～9/23）中，9 檔都有 65 列，close 與 volume 皆無 null。因此收盤序列與 c_price、20 日窗口對齊（量能對齊屬已知項目，不重複）
  - 來源：AUDIT_SCRATCH/work/G03gap/coverage.py
  - 來源：rc_audit_out.json（coverage）
- **AI 證據登錄（ai_research.build_evidence_registry）的 price_context 與 technical_context 12 個欄位，與研究脈絡（畫面）同名欄位**：2454、0050、6196 在 9/23 的 payload 與 ctx 逐欄相等（all_same＝true）
  - 來源：AUDIT_SCRATCH/work/G03gap/rc_audit2_out.json（ai_registry_0923）
- **market_context.advance_ratio（ai_research.py:141-144）**：公式 adv/(adv+dec+flat) 與 Dashboard 的畫面公式（Dashboard.tsx:234-235）及 abnormal_diagnostics.py:275-279 相同。9/23 實測 payload 為 0.407，與畫面公式算出的 0.407 一致（但輸入資料缺上櫃，見 P1 finding）
  - 來源：rc_audit2_out.json
- **比較頁共用同一日期與記憶化代理（comparison.py:209-221、59-74）**：讀程式碼：resolved_date 只決定一次，每一檔都以同一個 target_date 呼叫 get_research_context；proxy 只快取 get_snapshot(date)，不改變數值
  - 來源：backend/app/taiwan/comparison.py:209-221
- **研究卡與比較頁的百分比格式（TaiwanStockDetail.tsx:881-895、TaiwanStockCompare.tsx:135、344-358）**：讀程式碼：小數 ×100 後取 2 位並加 %，與後端小數欄位的語意一致
  - 來源：frontend/src/pages/TaiwanStockCompare.tsx:135

## 無法驗證的項目

- **MA5／MA20／MA60、月線乖離的第三方數值比對**：Yahoo 奇摩股市技術分析頁與玩股網技術線圖由前端動態產生，WebFetch 抓不到數值；Goodinfo 回 HTTP 403；本 session 的 WebSearch 額度（200）已用完。只抓到 HiStock 的多期漲跌幅（已列入 verified_ok）
- **ma60 所用 6～7 月收盤價的官方核對**：參考資料只有 2026-08、09 兩個月的 STOCK_DAY；6/30～7/31 的 store 收盤價沒有官方資料可比
- **TWSE／TPEx 股票與 ETF 升降單位的官方條文**：規定不得直連證交所與櫃買；維基百科「臺灣證券交易所」條目沒有列出升降單位表，WebSearch 額度也已用完。ETF tick 的 finding 改以交易所 MIS 官方五檔報價的價差作實證（0050 為 0.05 一檔、00878 為 0.01 一檔）
- **2026-09-28（教師節）是否休市**：只查到維基百科節日列表的摘要說教師節放假，沒有證交所行事曆佐證，屬推測。2026-09-25 中秋節已有來源
- **enrichment/index.py 的 parse_tpex_rows（prev_close＝close−Change）**：TPEx openapi 的 tpex_index 欄位名稱無法在不連櫃買中心的情況下確認；已下載的 indexInfo/inx 是不同端點。此函式實務上也只被以空陣列呼叫
- **store 成交量比官方少的成因**：差額都是整千股（例：2454 在 9/16 差 249,000 股），且只出現在當天寫入的分區，推測是 16:30～17:xx 時官方還沒計入鉅額等交易；沒有官方公布時點的文件可佐證
- **無成交日 close 為 null 時 MA／20 日報酬會跨越超過 20 個交易日（全市場 247 檔受影響，自選 9 檔 0 檔）**：這屬於「以有成交的 K 棒數計算」還是「以交易日數計算」的慣例選擇，無法取得台灣券商的權威定義來判定對錯
- **研究脈絡的 industry_context、institutional_context、margin_context、fundamentals／etf_context 各欄位**：不在本領域（已知的 5／20 日口徑已排除；產業與籌碼欄位由其他領域稽核）。本領域只核對它們進入 AI payload 時的閘門與值是否一致
