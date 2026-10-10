# 台股看板全面稽核與修正追蹤

> 原始稽核日期：2026-09-24。最新重核日期：2026-10-06；第 1 階段（回測可信度）全部完成後，於 2026-10-07 再次更新；第 2 階段（資料完整性與證券主檔）在程式上完成後，於 2026-10-07 第三次更新；同日晚間資料上線後第四次更新；2026-10-08 第 3 階段（盤中一致性）與第 6 階段（補回歸測試）在程式上完成後第五次更新；2026-10-10 第 4 階段（策略與選股）在程式上完成後第六次更新。
> 最新重核基準：`6582e9e`（重核當時的 HEAD，分支 `feat/market-filter-volume-patterns`）。第 1 階段已全部提交（主要在 `0bfa82f`，S8 在 `6515eb0`），第 2 階段已提交（程式 `b3a8007`、稽核文件 `d49296b`），資料寫入（主檔刷新、enriched 全量重新發佈、策略快取重算、`kline_daily` 殘留隔離）已在 2026-10-07 晚間完成。第 3、6 階段已提交（程式 `ac16ee5`、稽核文件 `b230f60`），之後 `2f691d5` 另把 21:30 以後寫入的同日日K分區視為定稿。第 4 階段的程式與測試在工作樹、尚未提交，本機資料也還沒上線；標為第 4 階段的證據行號對目前工作樹（HEAD `2f691d5` 加上這些修改）；標為第 3、6 階段的證據行號對 `ac16ee5`；第 2 階段更新的 finding 與本頁標為第 2 階段的證據，行號對 `b3a8007`；第 1 階段的證據，行號對第 1 階段完成時的工作樹；其餘行號對 `6582e9e`。工作樹另有 `frontend/pnpm-lock.yaml`、`frontend/vite.config.d.ts` 的無關修改，不作為程式現況證據。
> 行號偏移：`6582e9e` 之後改過的檔案，未更新列的 `6582e9e` 行號都可能已經偏移，請以函式名稱定位。第 1 階段在 `0bfa82f` 提交，改動較大的有 `backend/app/backtest/engine.py`、`factor.py`、`strategy.py`、`matrix.py`、`walkforward.py`，`backend/app/api/backtest.py`、`kline.py`、`intraday.py`，`backend/app/indicators/pipeline.py`，`backend/app/taiwan/market_rules.py`、`screener.py`、`enriched_refresh.py`、`detail_service.py`，`frontend/src/lib/api.ts`、`frontend/src/pages/StrategyBacktest.tsx`、`frontend/src/components/EChartsIntraday.tsx`，`backend/app/services/backtest.py` 已刪除。第 2、4 階段改過的檔案見 [recheck-2026-10-06.md](recheck-2026-10-06.md) 開頭；較早階段的證據引用到這些檔案的行號可能已經偏移。
> 本頁前半部是進度入口，194 條 finding 的逐條判定與現行證據在 [recheck-2026-10-06.md](recheck-2026-10-06.md)；後半部保留 2026-09-24 的原始稽核基線。原章節與附錄的程式碼行號只對 `ced12a5` 有效，不能當成目前位置。

## 目前結論（2026-10-10，第 4 階段程式完成後）

原稽核的 194 條有效 finding（被推翻的 1 條不計）已全部對照程式碼逐條重核；第 1 階段（回測可信度）完成後更新了其中 22 條，第 2 階段（資料完整性與證券主檔）又更新了 17 條，上線後 A01#5 再改判為已修正；第 3、6 階段再更新 41 條（已提交為 `ac16ee5`）；第 4 階段（策略與選股）依目前工作樹（HEAD `2f691d5` 加上尚未提交的修改）再更新 39 條。以下數字都由 [recheck-2026-10-06.md](recheck-2026-10-06.md) 的逐條表重算。

- 已修正 135 條，其中 133 條有直接測試、2 條待實測（收盤集合競價，A13#1、A13#7）；沒有只靠上游測試的條目；部分修正 7 條；未修正 52 條。第 4 階段之前是已修正 99、部分修正 6、未修正 89；第 3、6 階段之前是已修正 79、部分修正 3、未修正 112；第 2 階段之前是已修正 62、部分修正 8、未修正 124；第 1 階段之前是已修正 43、部分修正 11、未修正 140。
- 27 條 P1 全部已修正，而且都有直接測試：第 6 階段為 A02#0、A03#0、A04#0、A08#2、A09#0、A10#0 補上指標值與策略訊號的回歸測試。
- 根因層級：RC01–RC04、RC07–RC09、RC12–RC14、RC18、RC19、RC25、RC27、RC28、RC35、RC36 已關閉；RC11 程式已修，2 條待實測；RC05、RC06 主路徑已修，各剩 1 條殘留；RC10 主路徑已修，剩 3 條殘留（盤中整股量依決策 D3 保留並標示）；RC23、RC24 主路徑已修，各剩 1 條依決策保留的殘留（A08#9、A09#7）；RC20、RC22、RC29、RC31 與單項有部分進展；其餘 9 個根因（RC15–RC17、RC21、RC26、RC30、RC32–RC34）的 finding 全部未修正。RC25 的指標慣例只在程式上完成，已發佈的 enriched 要等上線時全量重新發佈。
- A01（股價資料）、A10（反轉策略，第 4 階段起）、A11（回測／成本）與 G06（商品分類）的 finding 全部已修正且有直接測試。`f404a85` 新增的回測頁（`/backtest`）讓回測 finding 從 UI 就會觸發，第 1 階段已把 A11 的 P1 與統計口徑全部修好。
- 第 2 階段的程式已完成並提交（`b3a8007`）：證券主檔每日刷新（驗證、保留下市、查詢 TPEx ETF 主被動型態）與新上市回補；主動式、CEOGEU、期貨信託 ETF 與 TDR 的分類；`listing_date` 改為 ISO；限股票策略不再選進 ETF；產業情報與異常診斷依交易所判定品質；`data_integrity` 改掃台股表、只回報；enriched 依 schema 與漲跌幅規則決定全量重建；`--symbols` 的上櫃日K改用 dailyQuotes；台股不再讀寫 `kline_daily`。
- 第 2 階段的資料已在 2026-10-07 晚間上線：16:30 排程的主檔刷新因上市 ETF 清單逾時失敗、日K因 TPEx 中斷連線失敗，沒有寫入任何不完整的資料；18:43 按設定頁「更新」後主檔刷新成功，enriched 全量重新發佈兩次，18:49 策略快取重算，`kline_daily` 殘留分區已隔離（見「修正順序規劃」的第 2 階段上線紀錄）。00847B 是否真的下市仍未找到公告佐證，列在已知缺口。之前估計 enriched 全量重新發佈要 40 分鐘是錯的：`data/backend.log*` 記錄的全量重寫，2026-10-01、10-02、10-06 是 48～57 秒，10-05 是 116 秒。
- 第 1 階段已完成並提交。S8 新上市規則已實作（A05#3 已修正）：初次上市（上櫃）一般股含創新板，自上市日起第 1–5 個交易日不設漲跌幅，依據是[證交所營業細則第 63 條第 2 項](https://twse-regulation.twse.com.tw/TW/law/DOC01_print.aspx?FLCODE=FL007304&FLNO=63)（2026-10-06 查證原文）與[證交所創新板交易制度](https://www.twse.com.tw/TIB/zh/mechanism.html)。enriched 已於 2026-10-07 全量重新發佈，已存的連板數與新上市期間的漲跌停訊號都已更正。
- 第 1 階段的待決事項都已決定並完成：S8 口徑、enriched 全量重新發佈、vectorbt 移除，以及回測最低手續費預設改為整股 20 元、零股 1 元。
- 第 0 階段（止血）已在 2026-10-06 完成：`tests/backtest` 回到全綠，回測頁補上口徑說明，零股模式不再把股數顯示成張數，選股頁 RSI＝0 正確顯示（A03#1）。
- 第 3 階段（盤中一致性）已提交（`ac16ee5`）：台股盤中列改走與盤後相同的批次管線（RC09）；開盤量比以至少 15 分鐘折算並標「推估」；盤中量標示為整股量、官方日K發佈後改用官方量；13:25–13:30 改為收盤集合競價、13:30 結果成為收盤（待實測）；TWSE 休市表、09:05 臨時休市偵測與「今日休市」開關；STOCK_DAY_ALL 快取；Yahoo 開盤價；休市日重新發佈不清空換手率；研究卡標示資料日期；分鐘量缺口另記、均價線涵蓋率、`connectNulls` 關閉。收盤集合競價預計 2026-10-12 實測。後續的 `2f691d5` 把 21:30 以後寫入的同日日K分區視為定稿（較早寫入的仍是 provisional，由 21:40 排程重抓），首頁晚間不再因此顯示資料不完整；這個門檻同樣預計 2026-10-12 核對（見「第 3、6 階段待辦」）。
- 第 6 階段（補回歸測試）已提交（`ac16ee5`）：指標值、策略訊號、關鍵價位、停牌列與盤中盤後一致性的回歸測試；當時仍未修正的 A02#4、A02#6、A03#2、A04#7、A04#8／A09#10、A08#6 與 A02#7 的殘留以 strict xfail 釘住。第 4 階段修好 A02#6、A03#2、A04#7 後，對應的 22 個 xfail 已轉為一般測試；仍以 7 個 strict xfail 釘住的是 A02#4（MACD 柱 ×2）、A04#8／A09#10（252 日年化）、A08#6（布林 ddof）與 A02#7 的殘留，都留給第 5 階段。
- 第 4 階段（策略與選股）程式完成、尚未提交，本機資料尚未上線：缺值不再當 0 分或排最前（RC13）、內建策略改用只看有效 K 線的滾動窗口（RC24）、指標暖機慣例與新股排除（RC25）、逐支策略條件與評分方向（RC23）、composite 合併口徑（RC28）、換手率分母與門檻（RC29）、流動性改用成交額（RC35）、各策略自己的進場訊號 ID（RC36），以及計畫中新發現的 N1–N5。使用者決策 D1–D13、實測影響與上線計畫見「修正順序規劃」的第 4 階段各段，驗證見「本輪驗證與限制」。2026-10-09 21:40 開發後端的排程曾以未提交的工作樹改寫部分本機資料（沒有資料遺失），經過見「第 4 階段上線（尚未執行）」。
- 沒有「未完成」的章節了：G02 的外幣面額（G02#3）已修正，G06 的 3 條全部修正。

### 狀態定義

章節狀態：

| 狀態 | 判準 |
|---|---|
| 已完成 | 章內所有 finding 都已修正，且有直接覆蓋原失效情境的測試或可重現驗證。 |
| 部分完成 | 章內至少一條 finding 已修正，但仍有已確認缺口。 |
| 未完成 | 章內沒有任何 finding 完整修正。 |

finding 判定（逐條見 [recheck-2026-10-06.md](recheck-2026-10-06.md)）：

| 判定 | 判準 |
|---|---|
| 已修正 | 原失效路徑已改，而且有直接斷言該情境的測試。 |
| 已修正（無測試） | 程式已改，但沒有測試直接斷言 finding 所說的那個量。 |
| 已修正（待實測） | 程式已改且有測試，但測試依據的是合成樣本，要等交易日實測確認。 |
| 部分修正 | 主要路徑已修，但仍有具體子情境未修。 |
| 未修正 | 原失效路徑仍在。 |

## 最新章節狀態

「已修正」欄含「已修正（待實測）」，括號內是其中待實測的條數；第 6 階段後已沒有「已修正（無測試）」。

| 章節 | 狀態 | 已修正 | 部分修正 | 未修正 | 已確認進展 | 主要缺口 |
|---|---|---|---|---|---|---|
| A01 股價資料 | 已完成 | 10 | 0 | 0 | 雙市場快照失敗拒存、同日初值回補、前復權與官方參考價、快照日期比對。第 2 階段：證券主檔每日刷新與新上市回補、`data_integrity` 改掃台股表、台股讀取端不再讀 `kline_daily`、`--symbols` 上櫃改用 dailyQuotes、enriched 依 schema 與漲跌幅規則全量重建；2026-10-07 上線後 7812 等新上市標的已有日K，`kline_daily` 殘留分區已隔離。 | 章內 finding 已全部修正；已知缺口見「第 2 階段的已知缺口」。 |
| A02 MA／MACD | 部分完成 | 7 | 0 | 2 | 前復權輸入；選股與自選頁改讀已發佈的指標；台股盤中列改走與盤後相同的管線（第 3 階段）；指標值與停牌列的回歸測試（第 6 階段）；EMA 以 SMA 起始、MACD 暖機期為 null（第 4 階段，已發佈值待上線全量重新發佈）。 | MACD 柱 2× 與命名（strict xfail，第 5 階段）；浮點相等。 |
| A03 KD／RSI | 部分完成 | 6 | 0 | 1 | Cutler RSI 已移除；選股頁 RSI＝0 正確顯示；自選頁 KDJ、RSI6、RSI24 有值；上市首日 RSI＝0 已消除；台股盤中 RSI 與盤後一致（第 3 階段）；跨分割的 KD／RSI 回歸測試（第 6 階段）；KD 從 50 起算、Wilder RSI 起始，K 線改讀已發佈的指標（第 4 階段）。 | RSV 0/0；漲跌皆為 0 時 RSI 給 0（第 5 階段）；非台股盤中路徑仍是舊起始方式。 |
| A04 Boll／ATR／量能 | 部分完成 | 10 | 1 | 1 | 前復權；選股與研究脈絡的量比改讀管線口徑；同日初值回補；台股盤中 60 日高低、布林與窗口和盤後一致；開盤量比至少以 15 分鐘折算；波動類指標的回歸測試（第 3、6 階段）；ATR 改用 Wilder 起始（第 4 階段）。 | 盤中量為整股量（依決策 D3 保留並標示）；252 日年化（strict xfail，第 5 階段）。 |
| A05 漲跌停／訊號 | 部分完成 | 11 | 1 | 3 | 除權息日漲跌停基準改用參考價；回測鎖死改讀已發佈訊號；連板數依檔位重算並已全量重新發佈；新上市前 5 日不設漲跌幅；10000 哨兵移除；逼近漲跌停改以前一日收盤；分時線與報價面板的國外 ETF、檔位與百分比；ETF 分類失敗不再回 500；檔位表收斂成單一實作。第 2 階段：主動式 ETF 依官方旗標分類、CEOGEU 改判未知並以稽核清單確認。第 3 階段：台股盤中 60 日高低、布林、MA60 與盤後一致。第 4 階段：MACD 暖機期不發佈交叉訊號。 | 檔位表的批次路徑沒有逐列帶交易日；跌停翻紅定義；布林 ddof；浮點相等。 |
| A06 換手率／市值／漲跌幅 | 部分完成 | 7 | 2 | 3 | 除權息日漲跌幅、同日初值回補、上櫃缺日拒存、股本刷新不再整檔覆寫；TDR 不再以原股數算換手率與市值（第 2 階段）；休市日重新發佈不再清空換手率，官方日K發佈後換手率改用官方量（第 3 階段）；換手率分母扣除私募股，各日的換手率在重新發佈時保留（第 4 階段）。 | 盤中換手率分子仍是整股量、盤中成交額刻意顯示「—」（依決策 D3 標示）；ETF 換手率與市值仍為 null（「不適用」的顯示延到第 5 階段）；萬億、股／張單位。 |
| A07 籌碼 | 部分完成 | 2 | 0 | 5 | 法人改用官方金額；法人初值回補。 | 詳細頁即時路徑券資比為空；缺列語意；選股器用舊列；未使用的因子程式；「近 5 日」卡含 1 日值。 |
| A08 趨勢策略 | 部分完成 | 9 | 1 | 2 | 前復權輸入；上櫃缺日拒存；策略訊號跨除息的回歸測試（第 6 階段）。第 4 階段：缺值不再給 0 分；只看有效 K 線的滾動窗口；30 個交易日的新股排除；macd_golden 改用共用 MACD 並宣告暖機；consolidation_breakout 的評分方向與量縮門檻；各策略自己的進場訊號 ID；流動性改用成交額；漲停鎖死的選股另加標籤。 | 漲停鎖死只加標籤、排名不變（A08#9，依決策 D8e 修訂版）；布林 ddof（strict xfail，第 5 階段）；「60 日最高」命名（RC17）。 |
| A09 動能策略 | 部分完成 | 9 | 1 | 2 | 前復權輸入；同日初值回補；策略訊號跨除息的回歸測試（第 6 階段）。第 4 階段：low_volatility_leader 改用 `vol_ratio_5d`、high_turnover_surge 在沒有換手率的日期明確拒絕、門檻改為 2%；只看有效 K 線的滾動窗口與 `valid_rolling_quantile`；lorentzian 固定 120 根候選窗口、選股與回測一致；factor_rank 改用名次百分位；composite 共用名次正規化；評分欄位的暖機。 | lorentzian 與原版的設計差異只以文件說明（A09#7）；布林 ddof 與收斂的自訂定義（A09#8）；252 日年化（A09#10，strict xfail，第 5 階段）。 |
| A10 反轉策略 | 已完成 | 16 | 0 | 0 | 前復權輸入；同日初值回補；限股票策略不再選進 ETF（第 2 階段，策略快取已於 2026-10-07 重算）；反轉與回檔策略跨除權息的回歸測試（第 6 階段）。第 4 階段：缺值不再給 0 分且排最後；`rsi_14`、`momentum_5d` 低者優先；bottom_reversal 的出場與爆量定義；n_day_low_reversal 改為盤中跌破、收盤收回；pullback_to_support 要求收盤 ≥ MA20；composite 合併口徑；流動性改用成交額；進場訊號 ID；新股排除；RSI 進場條件的暖機。 | 章內 finding 已全部修正（程式尚未提交、資料尚未上線）；已凍結的歷史選股快照不回改（D12）。 |
| A11 回測／成本 | 已完成 | 11 | 0 | 0 | 策略、優化、步進、mining 接上台股成本與交易單位，拒絕單一稅率覆蓋；舊回測路徑刪除；鎖死改讀已發佈訊號；股數與費用用原始價；觸價依升降單位取整；年化、Sharpe、profit factor、mc_maxdd 新口徑；walk-forward 依交易日；基準改用加權指數。 | 章內 finding 已全部修正；已知限制見「第 1 階段留下的已知缺口」。 |
| A12 選股／統計 | 部分完成 | 7 | 0 | 3 | 上櫃缺日拒存、除權息日參考價、法人金額、選股量比與 RSI 口徑、同日初值回補、逼近漲跌停改以前一日收盤；產業情報依交易所判定品質（第 2 階段）；選股器空值排最後、恆為 null 的 5 日欄位不能排序（第 4 階段）。 | 首頁推薦數重複加總；regime 與指數欄位。 |
| A13 即時行情 | 部分完成 | 7（2） | 1 | 0 | 上櫃缺日拒存；同日初值回補；收盤集合競價時段與 13:30 收盤結果進入即時路徑（待實測）；STOCK_DAY_ALL 快取、交易時段不再回退官方資料；分鐘量缺口另記；Yahoo 開盤價（第 3 階段）。 | 盤中量能仍為整股量（依決策 D3 保留並標示）；收盤集合競價待 2026-10-12 實測。 |
| G01 異常診斷 | 部分完成 | 3 | 0 | 9 | 除權息日參考價；同日初值回補；資料品質依交易所判定（第 2 階段）。 | SHORT_SURGE 標籤；零中位數不評估；先捨入再比門檻；母體與基期；最低融資門檻。 |
| G02 基本面／比較 | 部分完成 | 1 | 0 | 5 | 面額保留幣別與官方原文，TDR 股數改標原股（第 2 階段）。 | 基本面無快取；財報任一類別失敗整卡失敗；累計值未標示；10 個財務欄位讀 A 股 API；比較頁 null 一律顯示「不適用」。 |
| G03 研究脈絡 | 部分完成 | 4 | 0 | 6 | 上櫃缺日不再標 complete；同日初值回補；研究總狀態繼承市場完整性；ETF 的 tick size 改用 ETF 檔位；16:00 後與休市日顯示最新已存交易日並標示資料日期（第 3 階段）。 | 大盤基準卡永遠「--」；20 日高低未標收盤；AI 證據少大盤脈絡的時段反而變長；證據計數寫死。 |
| G04 關鍵價位 | 部分完成 | 3 | 0 | 8 | 前復權消除分割與除權息的機械缺口；`/levels` 的回歸測試（第 6 階段）；換手率缺值不再當 0，未滿整段視窗時改標「量能POC」（第 4 階段）。 | 換手率要逐日累積到整段視窗才會用到衰減模型；盤中樞軸點漂移；缺口回補判定；Keltner；未依升降單位取整；聚合方式；API 舊欄位名；簡體標籤。 |
| G05 圖表口徑 | 部分完成 | 9 | 0 | 2 | 日K與分時改用後端 `prev_close`；除權息日分時漲跌停線改用官方價；國外成分 ETF 不畫漲跌停線；漲跌停價依檔位取整；同日初值回補；量比N折算並標「推估」；均價線依涵蓋率顯示；分時不再連接缺口；迷你分時改用成交量加權均價（第 3 階段）；換手率改顯示後端的值，不再多乘 100（第 4 階段）。 | 十億元格式；成交量以股顯示。 |
| G06 商品分類 | 已完成 | 3 | 0 | 0 | 債券 ETF 已改判 bond。第 2 階段：TDR 以 `security_class` 標示、產業計入未分類；期貨信託 ETF 改判 futures／inverse；`listing_date` 一律 ISO；主檔每日刷新。 | 章內 finding 已全部修正；00980T 的成分範圍仍未確認。 |

## 已確認完成的主要修正

標「第 4 階段」的列，證據行號對目前工作樹（HEAD `2f691d5` 加上未提交的第 4 階段修改）；標「第 3 階段」「第 6 階段」的列對 `ac16ee5`；標「第 2 階段」的列，證據行號對 `b3a8007`；標「第 1 階段」的列，行號對第 1 階段完成時的工作樹；其餘列對 `6582e9e`。第 1 階段已全部提交（主要在 `0bfa82f`，S8 在 `6515eb0`）；第 2 階段在 `b3a8007` 提交，資料已於 2026-10-07 上線；第 3、6 階段在 `ac16ee5` 提交；第 4 階段尚未提交，資料尚未上線。

| 根因 | 狀態 | 主要證據 | 相關 commit |
|---|---|---|---|
| RC01 公司行動、參考價與前復權 | 已關閉；第 6 階段為 8 條補上指標值、策略訊號與關鍵價位的直接測試。第 1 階段補上 A11#2（回測以原始價計算股數與費用）與 G05#0（除權息日分時漲跌停線用官方價） | `backend/app/taiwan/enriched_refresh.py:105-135`、`backend/app/indicators/pipeline.py:387-400`；測試 `backend/tests/test_taiwan_reference_prices.py:132`、`backend/tests/test_taiwan_forward_adjustment.py:178`。第 1 階段：`backend/app/backtest/engine.py:67-72`、`backend/app/api/kline.py:357-413`；測試 `backend/tests/backtest/test_raw_price_sizing.py:143`、`backend/tests/test_kline_minute_price_limit_taiwan.py:106` | `b9a9d50`、`0bfa82f` |
| RC02 不完整雙市場資料被當成完成 | 已關閉；第 2 階段補上 A12#0、G01#1（產業情報與異常診斷依交易所判定品質，新增 provisional） | `backend/app/taiwan/providers/snapshot_provider.py:138-159`、`backend/app/taiwan/daily_store.py:294-314`；測試 `backend/tests/test_taiwan_snapshot_daily.py:242`。第 2 階段：`backend/app/taiwan/market_intelligence.py:111-145`、`backend/app/taiwan/industry_intelligence.py:270-289`、`backend/app/taiwan/abnormal_diagnostics.py:716-735`；測試 `backend/tests/test_stage2_quality_gates.py:213`、`:274` | `180c071`、`b3a8007` |
| RC03 同交易日初值不回補 | 已關閉 | `backend/app/taiwan/daily_store.py:50-57`、`:323-330`、`backend/app/taiwan/daily_update.py:374-385`；測試 `backend/tests/test_taiwan_provisional_refresh.py:145` | `584fe89`、`612ea7a` |
| RC04 市場法人以股數跨證券彙總 | 已關閉 | `backend/app/taiwan/market_intelligence.py:309-331`；測試 `backend/tests/test_taiwan_market_institutional.py:785` | `9330088` |
| RC05 各頁各自重算指標 | 主路徑已修；殘留 A06#11 | `backend/app/taiwan/screener.py:230`、`backend/app/taiwan/research_context.py:454-455`、`backend/app/taiwan/watchlist_enrichment.py:153-161`；測試 `backend/tests/test_taiwan_screener.py:189`、`backend/tests/test_taiwan_watchlist_enrichment.py:358` | `9330088` |
| RC06 漲跌停價與升降單位多份實作（第 1 階段） | 主路徑已修；殘留 A05#14（批次路徑沒有逐列帶交易日）。連板數已隨 2026-10-07 的全量重新發佈更正 | `backend/app/taiwan/market_rules.py:188-262`、`backend/app/backtest/matrix.py:1443-1472`、`backend/app/taiwan/enriched_refresh.py:265-289`、`backend/app/api/kline.py:298-327`；測試 `backend/tests/test_taiwan_market_rules.py:329`、`backend/tests/backtest/test_limit_lock_taiwan.py:175`、`backend/tests/test_taiwan_limit_streak.py:107` | `0bfa82f` |
| RC07 回測台股費稅與交易單位 | 已關閉；第 1 階段補上拒絕單一稅率覆蓋、刪除舊回測路徑、觸價依升降單位取整 | `fea6532`（行號對 `6582e9e`）：`backend/app/backtest/strategy.py:525-590`、`backend/app/backtest/engine.py:162-181`；測試 `backend/tests/backtest/test_taiwan_execution.py:57-106`。第 1 階段（行號對工作樹）：`backend/app/backtest/strategy.py:569-642`（拒絕單一稅率覆蓋在 `:585-586`）、`backend/app/backtest/engine.py:79-111`、`:213-245`；測試 `backend/tests/backtest/test_taiwan_execution.py:128`、`backend/tests/backtest/test_trigger_tick_rounding.py:147` | `fea6532`、`0bfa82f` |
| RC08 成交日使用不可知資訊 | 已關閉 | `backend/app/backtest/strategy.py:516-522`、`backend/app/backtest/engine.py:1253-1257`、`:2361-2366`；測試 `backend/tests/backtest/test_taiwan_execution.py:540-762` | `fea6532` |
| RC12 漲跌幅限制不分商品別與上市狀態（第 1 階段） | 已關閉：國外成分 ETF 不畫漲跌停線（S4），新上市前 5 日不設漲跌幅（S8） | `backend/app/taiwan/universe/models.py:140-232`、`backend/app/taiwan/market_rules.py:334-357`、`backend/app/api/kline.py:298-327`；測試 `backend/tests/test_taiwan_limit_ipo.py:121`、`backend/tests/test_kline_minute_price_limit_taiwan.py:140`、`:188` | `0bfa82f`、`6515eb0` |
| RC19 逼近漲跌停以當日收盤為基準（第 1 階段） | 已關閉 | `backend/app/taiwan/screener.py:320-419`；測試 `backend/tests/test_taiwan_screener.py:415`、`:471` | `0bfa82f` |
| 回測統計口徑與基準（第 1 階段，RC31、RC22 的回測部分） | A11#6、#7、#9 已修正；RC31 剩 `annual_vol_20d` 的 252 年化（A04#8、A09#10） | `backend/app/backtest/engine.py:488-575`、`backend/app/backtest/walkforward.py:35-72`、`backend/app/backtest/strategy.py:2060-2098`；測試 `backend/tests/backtest/test_stats_methodology.py:77`、`backend/tests/backtest/test_walkforward.py:57`、`backend/tests/backtest/test_benchmark_taiex.py:182` | `0bfa82f` |
| RC14 證券主檔與商品分類（第 2 階段） | 已關閉（程式）：主檔每日刷新、驗證失敗保留舊檔、保留下市；新上市自動回補；主動式、CEOGEU、期貨信託 ETF 分類；`listing_date` ISO；限股票策略不選 ETF。本機主檔已於 2026-10-07 18:43 刷新 | `backend/app/taiwan/universe/service.py:579-678`、`backend/app/taiwan/daily_update.py:158-294`、`backend/app/taiwan/universe/adapters.py:198-200`、`:563-605`、`backend/app/strategy/engine.py:643-674`；測試 `backend/tests/test_stage2_master_refresh.py:221`、`backend/tests/test_stage2_master_wiring.py:296`、`backend/tests/test_stage2_etf_classification.py:55`、`backend/tests/test_stage2_strategy_asset_types.py:160` | `b3a8007` |
| RC27 TDR 與外幣面額（第 2 階段） | 已關閉：TDR 以 `security_class` 標示，產業計入未分類，不算換手率與市值，證交稅 0.1%；面額保留幣別與官方原文 | `backend/app/taiwan/universe/models.py:162-180`、`backend/app/taiwan/share_capital_store.py:211-229`、`backend/app/taiwan/fundamentals.py:60-77`、`backend/app/taiwan/market_rules.py:127-129`；測試 `backend/tests/test_stage2_tdr.py:73`、`backend/tests/test_stage2_tdr_consumers.py:100`、`:515` | `b3a8007` |
| RC22 A 股遺留表（第 2 階段的部分） | A01#4 已修正（`data_integrity` 改掃台股表、只回報，不再擋即時行情）；A01#5 已修正（台股讀取端改讀 `taiwan/daily`，殘留分區已於 2026-10-07 隔離到 `data/_quarantine/kline_daily-2026-09/`）；A12#8、G02#1 未動 | `backend/app/services/data_integrity.py:95-138`、`backend/app/repository.py:1924-1954`、`backend/app/services/quote_service.py:870-877`、`:1753-1771`；測試 `backend/tests/test_stage2_data_integrity.py:98`、`backend/tests/test_stage2_kline_daily_readers.py:62` | `b3a8007` |
| enriched 全量重建條件、`--symbols` 上櫃口徑（第 2 階段，A01#6、A01#9） | 已修正：歷史分區 schema 或窗口外標的的漲跌幅規則改變就全量重建；`--symbols` 的上櫃日K改用 dailyQuotes、不覆寫已存列，主檔中已下市的上櫃標的也可回補，寫入後 enriched 從最早寫入日起重新發佈 | `backend/app/taiwan/enriched_refresh.py:119-174`、`backend/app/taiwan/daily_refresh.py:289-315`、`:382-398`；測試 `backend/tests/test_stage2_enriched_triggers.py:148`、`:161`、`backend/tests/test_stage2_symbols_backfill.py:233`、`:255`、`:569` | `b3a8007` |
| RC09 盤中增量路徑另有一套遞推（第 3 階段） | 已關閉（台股部署）：台股盤中列改走與盤後相同的批次管線，逐欄相同 | `backend/app/taiwan/live_enriched.py:353-396`、`backend/app/services/quote_service.py:1848-1851`、`:1880-1884`；測試 `backend/tests/test_stage3_live_parity.py:383`、`backend/tests/test_stage6_intraday_consistency.py:285` | `ac16ee5` |
| RC11 交易時段與休市日曆（第 3 階段） | 程式已修，A13#1、A13#7 待實測 | `backend/app/taiwan/realtime/calendar.py:142-147`、`backend/app/taiwan/realtime/mis_provider.py:62-84`、`backend/app/services/quote_service.py:618-647`、`backend/app/taiwan/realtime/holidays.py`；測試 `backend/tests/test_stage3_session_model.py:137`、`backend/tests/test_stage3_closing_auction.py:41`、`backend/tests/test_stage3_holidays.py` | `ac16ee5` |
| RC18 分鐘資料與分時圖（第 3 階段） | 已關閉：缺口量另記、均價線涵蓋率、`connectNulls` 關閉、迷你分時加權均價 | `backend/app/taiwan/realtime/minute_recorder.py:220-224`、`frontend/src/lib/intraday-chart.ts:32-48`；測試 `backend/tests/test_stage3_minute_gaps.py:63`、`frontend/src/components/EChartsIntraday.gaps.test.tsx:52` | `ac16ee5` |
| RC10 盤中量能（第 3 階段） | 主路徑已修：開盤量比折算（A04#6、G05#1）；盤中整股量依決策 D3 保留並標示（A04#5、A13#3、A06#3、A06#11 部分修正） | `backend/app/taiwan/realtime/calendar.py:150-188`、`backend/app/taiwan/watchlist_enrichment.py:95-108`；測試 `backend/tests/test_stage3_volume_scaling.py:43`、`backend/tests/test_stage3_volume_convention.py:125` | `ac16ee5` |
| 回歸測試（第 6 階段） | RC01 的 8 條與 A02#7、A04#9 補上直接測試；當時仍未修正的 7 條以 strict xfail 釘住，其中 A02#6、A03#2、A04#7 在第 4 階段修好，22 個 xfail 已轉為一般測試 | `backend/tests/test_stage6_indicator_values.py`、`test_stage6_strategy_signals.py`、`test_stage6_levels.py`、`test_stage6_halt_rows.py`、`test_stage6_intraday_consistency.py` | `ac16ee5` |
| RC13 缺值當成 0 分或排最前（第 4 階段） | 已關閉：缺評分輸入的候選保留、分數為 null、排最後並標出缺值欄位，分級為觀察（`score_unavailable`）；0 分是合法的最低分；選股器空值排最後；關鍵價位缺換手率時改標量能分布 | `backend/app/backtest/matrix.py:4134-4135`、`backend/app/strategy/engine.py:1428-1437`、`:1752-1762`、`:1984-2026`、`backend/app/services/pick_grades.py:124-125`、`backend/app/taiwan/screener.py:617`、`backend/app/indicators/levels.py:108-113`；測試 `backend/tests/test_stage4_missing_scores.py:142`、`:207`、`backend/tests/test_stage4_missing_scoring_fields.py:116`、`backend/tests/test_pick_grades.py:79`、`backend/tests/test_stage4_rc13_consumers.py:33`、`:178` | 工作樹（未提交） |
| RC23 策略條件與評分方向（第 4 階段） | 主路徑已修：bottom_reversal 的出場與爆量、n_day_low_reversal 改為盤中跌破收盤收回、pullback_to_support 要求收盤 ≥ MA20、consolidation_breakout 的評分方向與量縮門檻、反轉策略的 `rsi_14`／`momentum_5d` 低者優先（META `scoring_directions`）、factor_rank_research 改用名次百分位；殘留 A08#9（漲停鎖死只加標籤） | `backend/app/strategy/builtin/bottom_reversal.py:126-131`、`:162-167`、`n_day_low_reversal.py:71-72`、`pullback_to_support.py:83`、`consolidation_breakout.py:55-58`、`:74`、`factor_rank_research.py:197-237`、`backend/app/strategy/scoring.py:140-175`、`backend/app/services/pick_grades.py:137`；測試 `backend/tests/test_stage4_strategy_conditions.py:100`、`:139`、`:158`、`:208`、`:438`、`:609`、`backend/tests/backtest/test_stage4_factor_rank_research.py:69`、`:203` | 工作樹（未提交） |
| RC24 滾動窗口遇缺列（第 4 階段） | 主路徑已修：新增 `valid_rolling_quantile`，內建策略全部改用只看有效 K 線的 `valid_rolling_*`（守門測試確認沒有例外），lorentzian 的 0.5 佔位特徵已移除；殘留 A09#7（與原版的設計差異只以文件說明） | `backend/app/backtest/matrix.py:3520`、`:3734-3830`、`backend/app/strategy/builtin/consolidation_breakout.py:106-107`、`bollinger_squeeze.py:100`；測試 `backend/tests/backtest/test_stage4_valid_windows.py:128`、`:325`、`:587`、`backend/tests/backtest/test_stage4_lorentzian_knn.py:193` | 工作樹（未提交） |
| RC25 暖機、歷史長度與新股排除（第 4 階段） | 已關閉（程式）：EMA 以 SMA 起始、MACD 暖機期為 null、Wilder RSI 與 ATR、KD 從 50 起算（決策 D7），以 `INDICATOR_DEFINITION_VERSION = 2` 觸發一次全量重新發佈；暖機根數依種子權重推算；`exclude_new_days` 以 30 個交易日實作（D6）；lorentzian 固定 120 根候選窗口（D13）；K 線讀已發佈的指標。已發佈的值要等上線 | `backend/app/indicators/pipeline.py:61`、`:285-333`、`backend/app/strategy/scoring.py:90-123`、`backend/app/strategy/listing_age.py:27-96`、`backend/app/strategy/builtin/lorentzian_knn.py:45`、`backend/app/repository.py:1621-1625`、`backend/app/taiwan/enriched_refresh.py:301-317`；測試 `backend/tests/test_stage4_indicator_conventions.py:192`、`:203`、`:257`、`backend/tests/test_stage4_listing_age.py:196`、`:325`、`backend/tests/backtest/test_stage4_lorentzian_knn.py:169`、`:355`、`backend/tests/test_stage4_kline_published.py:96`、`backend/tests/test_stage6_indicator_values.py:550`、`:559` | 工作樹（未提交） |
| RC28 composite 合併口徑（第 4 階段） | 已關閉：選股與回測共用 `rank_normalize`（同分取平均名次、單一候選得 0.5），分母是全部子策略的權重（決策 D9，intersect 同樣）。另外子策略在 composite 的資產範圍內執行，composite 的基本過濾在選股與回測一致，composite 也遵守股票池 | `backend/app/strategy/composite.py:37-73`、`:131-143`、`:226-239`；測試 `backend/tests/test_stage4_composite.py:87`、`:93`、`:115`、`:140`、`:188` | 工作樹（未提交） |
| RC29 換手率（第 4 階段） | 部分進展：分母改為已發行普通股扣除私募股（決策 D4）；各日的換手率從當天的發佈起保留（D5）；high_turnover_surge 門檻 2%，沒有換手率的日期與回測明確拒絕（D3）；前端改顯示後端的換手率；ETF 換手率仍為 null（A06#8 延到第 5 階段） | `backend/app/taiwan/share_capital_store.py:271-286`、`:291-330`、`backend/app/taiwan/enriched_refresh.py:198-229`、`backend/app/strategy/builtin/high_turnover_surge.py:15`、`:63`、`frontend/src/lib/turnoverRate.ts:6-10`；測試 `backend/tests/test_stage4_turnover_denominator.py:67`、`backend/tests/test_stage4_turnover_history.py:251`、`backend/tests/test_stage4_high_turnover_refusal.py:123`、`:139`、`:275`、`frontend/src/lib/turnoverRate.test.ts:7` | 工作樹（未提交） |
| RC35、RC36 流動性與進場訊號（第 4 階段） | 已關閉：swing_trend_confirmation、pullback_resume 的流動性改為成交額 ≥ 1 億元（D10）；bullish_alignment、pullback_ma20_bounce、pullback_to_support 改用自己的進場訊號 ID，標籤集中在 `field_labels.py` | `backend/app/strategy/builtin/swing_trend_confirmation.py:56-62`、`pullback_resume.py:90-96`、`bullish_alignment.py:43`、`backend/app/strategy/field_labels.py:33-35`；測試 `backend/tests/test_stage4_strategy_conditions.py:289`、`:302`、`:328`、`backend/tests/test_monitor_localization.py:97` | 工作樹（未提交） |
| 計畫中新發現的 N1–N5（第 4 階段） | N1 選股歷史改以交易日計（所需根數加 60 個交易日），歷史快取從目標日往回算；N2 策略讀已發佈的列，重算的列也帶已發佈的漲跌停旗標；N3 過濾上下限遇缺值即排除、欄位不存在就報錯；N4 回測撮合對缺分數安全（排最後、不參與加權、紀錄為 null）；N5 0 分不再判觀察（D11），已凍結的 0 分與 null 歷史紀錄不回改（D12，部分修正） | `backend/app/services/screener.py:609-618`、`backend/app/repository.py:1316-1333`、`:103`、`backend/app/backtest/matrix.py:4626-4650`、`backend/app/backtest/engine.py:146-174`、`backend/app/services/pick_grades.py:89-90`；測試 `backend/tests/test_stage4_history_warmup.py:93`、`:118`、`backend/tests/test_stage4_limit_flags.py:148`、`backend/tests/test_stage4_missing_scores.py:225`、`:291`、`backend/tests/test_pick_grades.py:66` | 工作樹（未提交） |
| 市場完整性二次閘門 | universe、上市／上櫃覆蓋、provisional 與研究狀態已納入完成判定；覆蓋率門檻在 `6582e9e` 放寬為 ≥98%。第 2 階段改以 `listing_date` 與 `delisted_at` 推算目標日的母體，不再要求主檔時點早於目標日（`backend/app/taiwan/market_intelligence.py:70-108`；測試 `backend/tests/test_stage2_master_refresh.py:620`、`:631`） | `backend/app/taiwan/market_intelligence.py:703-718`、`backend/app/taiwan/research_context.py:781-793`；測試 `backend/tests/test_taiwan_market_intelligence.py`、`backend/tests/test_taiwan_research_context.py:388-416` | `fea6532`、`6582e9e` |

`06b173c` 的重試只涵蓋 `snapshot_provider.py`（MI_INDEX、TPEx dailyQuotes）與 `share_capital_store.py`，不算 RC20 的進展。以下是新能力，不等於關閉既有 finding：`c611a3f` 的市場濾網與量價型態、`ba2e007` 與 `e9453b5` 的推薦歷史與訊號後報酬，以及 `f404a85` 的回測頁。

## 修正順序規劃（2026-10-06）

排序依據有三點：會不會給出錯誤的金融數字；使用者每天會不會碰到（即時行情開啟，自選含沒有漲跌幅限制的 00885、00893）；修好一項能不能順便解掉其他項。各項的證據與行號見下一節「目前待修正項目」。

| 階段 | 主題 | 內容 | 前置與理由 |
|---|---|---|---|
| 0 | 止血（2026-10-06 已完成） | 修 `test_worker_process.py` 的 5 個失敗測試，改成不送 `stamp_tax_pct`。回測頁補上口徑說明：年化 252 日、Sharpe rf=0、每邊 5 bps 滑價、明細為前復權價；零股模式的「張數」改成「股數」。修前端 RSI＝0 顯示成「-」（A03#1）。 | 測試不全綠，後續驗收都不可信。回測頁已上線，數字修好之前至少不要誤導使用者。完成驗證：`tests/backtest` 全目錄 `308 passed`；`frontend/` 的 `StrategyBacktest.test.tsx`、`TaiwanScreener.test.tsx` 共 `67 passed`；`pnpm build` 退出碼 0。回測頁的口徑說明在第 1 階段已依新口徑改寫。 |
| 1 | 回測可信度（2026-10-07 已完成） | 已完成：<br>1. 升降單位與漲跌停價收斂成 `market_rules.py` 的單一實作（S1）；回測鎖死改讀 enriched 已發佈的訊號，漲跌幅規則未知的標的排除（S2）；管線的漲跌停訊號與連板數改用同一模型，10000 哨兵移除（S3）；分時 API、分時線、報價面板、詳細頁與研究脈絡改由後端依檔位計算（S4）。<br>2. 選股「逼近漲跌停」改以前一日收盤或官方參考價為基準（S5）。<br>3. 策略回測拒絕單一稅率覆蓋（S6）；因子回測、舊 `/api/backtest/run` 與 legacy `simulate()` 已刪除（S7）。<br>4. 股數、手續費與交易明細改用當時的原始價（S9）；觸價依升降單位取整（S10）。<br>5. 年化改依日曆天數、Sharpe 可扣無風險利率、profit factor 與 mc_maxdd 新口徑（S11）；walk-forward 改以交易日切分（S12）；基準改用加權指數（S13）。<br>6. 新上市前 5 日不設漲跌幅（S8）：初次上市（上櫃）一般股含創新板適用；同代號在上市日前已有本機日K者視為轉上市，照 10%；TDR 照原規則；`listing_date` 未知時一律當成「未知」。<br>7. enriched 於 2026-10-07 00:28 以 `--full` 全量重新發佈，發佈前已備份；回測最低手續費預設改為整股 20 元、零股 1 元；vectorbt 已移除。 | 第 1 階段結束後，P1 只剩 A12#0（第 2 階段）。 |
| 2 | 資料完整性與證券主檔（2026-10-07 已完成並上線） | 已完成（程式在 `b3a8007`，資料於 2026-10-07 晚間上線）：<br>1. 證券主檔刷新（A01#8）：交易日台北時間 16:00 後、目標日是今天時，16:30／21:40 排程與設定頁「更新」按鈕刷新主檔，當天已有完整成功（TPEx ETF 型態查完）就不再刷新；驗證不過就保留舊檔；官方清單沒有的標的保留為 `delisted`；最近 20 個分區內新上市的標的，在第一筆已存日K之前缺的日子會自動回補。<br>2. 分類：主動式 ETF（A05#4）、CEOGEU 與稽核清單（A05#5）、期貨信託 ETF（G06#1）、TDR（RC27）、`listing_date` ISO（G06#2）；限股票策略不選 ETF（A10#10）。<br>3. 產業情報與異常診斷的資料品質依交易所判定，新增 provisional（A12#0、G01#1）。<br>4. `data_integrity` 改掃台股資料表、只回報，移除完整性檢查造成的 409 與修復任務（A01#4；報價服務暫停時的 409 仍保留，`backend/app/api/settings.py:831-833`）；台股讀取端不再讀 `kline_daily`（A01#5、A02#5、A04#11）。<br>5. enriched 依 schema 與漲跌幅規則決定全量重建（A01#6）；`--symbols` 的上櫃日K改用 dailyQuotes（A01#9）。<br>6. 證交稅：TDR 0.1%（依財政部函釋；當沖同率是我們對第 2 條之 2 的解讀）；被動債券 ETF 免稅到 2026-12-31，之後依現行法改課 ETF 稅率 0.1%，回測不再從 2027 年起出錯；股票當沖減半到 2027-12-31，之後回到 0.3%。 | P1 已全部修正。上線紀錄見本節下方，已知缺口見「第 2 階段的已知缺口」。 |
| 3 | 盤中一致性（2026-10-08 程式完成並提交為 `ac16ee5`，待實測） | 已完成（程式）：<br>1. RC09（決策 D1）：台股盤中列改走與盤後相同的批次管線（`backend/app/taiwan/live_enriched.py`，最多 1200 個已存交易日加當日K；報價推得的除權息事件只在已存資料到前一交易日時才採用），非台股維持原遞推。<br>2. RC10：開盤量比以至少 15 分鐘折算、標「推估」（D2）；盤中量標示為整股量，官方日K發佈後改用官方量，盤中成交額顯示「—」（D3）。<br>3. RC11：13:25–13:30 為收盤集合競價，只暫停監控與提醒；MIS 報價分為 `TRIAL_MATCH`（試撮）與 `CLOSE_FINAL`（13:30 收盤結果），收盤同步要等池內當日有成交的標的都拿到 `CLOSE_FINAL`，最晚重試到 14:30（D4）；TWSE 休市表、09:05 仍無當日報價時改為每 5 分鐘探測一次、設定頁「今日休市」開關（D5）；休市日重新發佈不清空換手率；研究卡標示資料日期（D7）。<br>4. RC18：分鐘量缺口另記為未歸屬量，均價線顯示涵蓋率、低於 80% 隱藏，`connectNulls` 關閉，迷你分時改用成交量加權均價（D6）。<br>5. RC20、A13#5：STOCK_DAY_ALL 10 分鐘快取、交易時段不回退官方資料；Yahoo 開盤價改取當日K棒。<br>6. 順帶修正 `repository.get_minute_by_dates` 在 Windows 上永遠回空表的既有錯誤。 | 收盤集合競價只用合成樣本測過（2026-10-08 沒錄到 13:25–13:30 的 MIS 資料），預計 2026-10-12 實測。程式已提交為 `ac16ee5`；後續 `2f691d5` 把 21:30 以後寫入的同日日K分區視為定稿。 |
| 4 | 策略與選股（2026-10-10 程式完成，尚未提交，本機資料尚未上線） | 已完成（程式）：<br>1. RC13（決策 D1、D11）：缺評分輸入的候選保留、分數為 null，矩陣、Polars 選股、Polars 回測與 composite 用同一個規則，所有路徑都排最後、同分依代號；列上標出缺值的評分欄位（`missing_scoring_fields`，composite 另列各子策略），滑鼠提示「評分欄位缺值：…」；分級為「觀察・資料不足」（`score_unavailable`），0 分照名次分級；選股器空值排最後、恆為 null 的 5 日欄位不能排序；關鍵價位缺換手率時改標量能分布。<br>2. RC24：新增 `valid_rolling_quantile`，內建策略全部改用只看有效 K 線的窗口；原始的 `rolling_*` 只留給自訂策略。<br>3. RC25（決策 D6、D7、D13）：EMA 以 SMA 起始、MACD 暖機期為 null（DIF 前 25 根、DEA 前 33 根）、Wilder RSI 與 ATR、KD 從 50 起算，發佈端與矩陣共用同一組函式；`INDICATOR_DEFINITION_VERSION = 2` 與因子紀錄不一致時，下一次發佈全量重寫並保留換手率；遞迴指標依種子權重低於 1e-4 推算暖機根數；`exclude_new_days` 以 30 個交易日實作；macd_golden 改用共用 MACD；lorentzian 固定 120 根有效 K 線的候選窗口；K 線與歷史快取讀已發佈的指標。<br>4. RC23（決策 D2、D8）：bullish_alignment、low_volatility_leader、pullback_to_support 的換手率評分改為 `vol_ratio_5d`；D8a–g 的條件與方向（見下方決策）；factor_rank_research 改用名次百分位，並在基本過濾之後才取前 20 名；META 新增 `scoring_directions`。<br>5. RC28（決策 D9）：選股與回測共用 `rank_normalize`，分母是全部子策略的權重；子策略在 composite 的資產範圍內執行、composite 遵守股票池，基本過濾在選股與回測一致。<br>6. RC29（決策 D3–D5）：換手率分母改為已發行普通股扣除私募股；各日的換手率從當天的發佈起保留、不回推；high_turnover_surge 門檻 2%；需要換手率的策略，在合格標的覆蓋率低於 50% 的交易日拒絕（選股、監控與回測同一規則，回測另列覆蓋率不足的交易所）；前端改顯示後端的換手率。<br>7. RC35（決策 D10）、RC36：流動性改為成交額 ≥ 1 億元；三支策略改用自己的進場訊號 ID，標籤集中在 `backend/app/strategy/field_labels.py`。<br>8. 計畫中新發現的 N1–N5：見「已確認完成的主要修正」的同名列。<br>9. 一支策略載入失敗時，其他策略照常載入，失敗的列在 `load_errors`（清單畫面留給第 5 階段）。<br>10. 順帶修正的既有錯誤（S10a、R-E2、R-G 實測，HEAD 也會發生）：單一策略 API 對三支用換手率評分的策略回 400（unsupported matrix feature: turnover_rate）；K 線回傳約 300 天的重算值而不是已發佈值；歷史快取從快取的最後一天而不是目標日往回算；台股 Polars 回測因 instruments 表為空、缺漲跌停訊號欄而失敗；設了市值上下限的策略在台股回測時載入失敗，已註冊這類策略時連其他矩陣回測都會失敗（R-G 改為選股與回測一致：沒有股本資料時兩邊都忽略市值上下限）。 | 要先完成第 2 階段的商品分類。新指標慣例只在程式上完成，已發佈的 enriched 要等上線時全量重新發佈；上線計畫見本節下方「第 4 階段上線（尚未執行）」，已知缺口見「第 4 階段的已知缺口與延後項目」。 |
| 5 | 顯示、標示與次要模組 | 大盤基準卡（RC15）、名稱與說明（RC17）、顯示格式（RC16）、關鍵價位（RC21）、基本面快取與容錯（G02）、詳細頁即時路徑券資比為空（A07#1，P2）、`annual_vol_20d` 的年化（A04#8、A09#10）、首頁推薦數重複加總（A12#6）、未使用的籌碼因子程式（A07#5），以及 RC26、RC30、RC32、RC33、RC34。第 4 階段延過來的：ETF 換手率顯示「不適用」（A06#8）、MACD 柱 ×2（A02#4）、布林 σ（RC30）、漲跌皆為 0 時的 RSI、回測頁 `exit_reason` 顯示原始 ID、無法載入的策略清單（`load_errors`）、composite 編輯器與基本過濾的設定畫面、績效頁標示策略邏輯變更日（D12），詳見「第 4 階段的已知缺口與延後項目」。 | 多屬 P3，或只影響單一畫面。RC15 的資料已經有了，可以提前做。 |
| 6 | 補回歸測試（2026-10-08 已完成，已提交為 `ac16ee5`） | 已完成：A02#0、A03#0、A04#0 的指標值（`backend/tests/test_stage6_indicator_values.py`）；A08#2、A09#0、A10#0 的策略訊號（`test_stage6_strategy_signals.py`）；G04#2、G04#4 的關鍵價位（`test_stage6_levels.py`）；A02#7、A04#9 的停牌列（`test_stage6_halt_rows.py`）；盤中與盤後一致性（`test_stage6_intraday_consistency.py`）。當時仍未修正的 A02#4、A02#6、A03#2、A04#7、A04#8／A09#10、A08#6 與 A02#7 的殘留以 strict xfail 釘住；第 4 階段修好 A02#6、A03#2、A04#7，22 個 xfail 已轉為一般測試，剩 7 個。 | 第 3 階段開工前完成，第 3、4 階段全程保持通過。 |

需要先決定的事項（RC09 的修法已在第 3 階段決定為共用管線，見下方 D1）：

1. AI 研究遇到 `provisional` 資料時，要帶入大盤脈絡並加註提示，還是維持排除？這決定 G03#6 的修法。第 2 階段已讓產業脈絡為 provisional 時帶入 AI 並註明是初值（`backend/app/taiwan/ai_research.py:164`），大盤脈絡仍只接受 current／complete；16:30～21:40 融資未公布時，兩者都是 partial，仍被排除。

第 1 階段的待決事項都已決定並完成：S8 的 3 個口徑問題、enriched 全量重新發佈、vectorbt 移除（`backend/pyproject.toml` 保留空的 backtest extra 以維持相容），以及最低手續費預設（整股 20 元、零股 1 元，有傳值時以傳值為準，mining 維持 0）。

第 3 階段的決策（2026-10-07 決定，D1–D4 由使用者選定，D5–D7 採用建議）：

- D1 RC09：台股盤中列改走共用的批次管線，非台股維持 `compute_enriched_today`。原規劃上限 750 個交易日，實作為 1200 個（`backend/app/taiwan/live_enriched.py:37`）。
- D2 開盤量比：經過時間至少以 15 分鐘計（倍數最多 18），量比數值與 `signal_volume_surge` 都折算，盤中標「推估」。
- D3 盤中量：只用 MIS 整股量並標示「盤中為整股量（不含零股、鉅額、盤後定價）」；官方日K發佈後改用官方量與成交額；盤中成交額顯示「—」。S0 的 MIS 探測之後，再評估是否加入 MIS 零股量。
- D4 收盤集合競價 13:25–13:30：暫停監控與提醒，以 13:30 結果評估一次；13:30 的競價量成為獨立的 13:30 分鐘K。
- D5 休市日：每年抓 TWSE 官方休市表並快取，未公布的年度視為未驗證；09:05 仍沒有當日報價時推定臨時休市，之後每 5 分鐘探測；設定頁可手動標示「今日休市」。
- D6 分鐘量缺口：另記為未歸屬量；均價線顯示涵蓋率，低於 80% 隱藏。
- D7 研究卡：16:00 到盤後發佈前與休市日顯示最新已存交易日並標示「資料日期」；AI 研究的閘門不變。

第 4 階段的決策（2026-10-09 決定，使用者採用建議方案；D8e 於同日由使用者修訂）：

- D1 缺評分輸入：保留候選、分數為 null、標出缺值欄位，所有路徑（含回測）排最後，分級為觀察（`score_unavailable`）。
- D2 bullish_alignment、low_volatility_leader、pullback_to_support 的 `turnover_rate`（權重 0.3）改為 `vol_ratio_5d`；pullback_to_support 經使用者確認為低者優先（策略本意，雖然樣本內高者優先較好）。
- D3 high_turnover_surge：預設門檻 2.0%（約 p90），說明寫明分母（實作依 D4 寫成已發行普通股扣除私募股）；沒有換手率歷史的日期與回測一律拒絕，等歷史累積。
- D4 換手率分母：官方欄位確認後採已發行普通股扣除私募股（TWSE `私募股數`、TPEx `PrivateStock.shares`，已確認）；ETF 為 null，畫面顯示「不適用」留給第 5 階段。
- D5 換手率歷史：從現在起保留各日當天發佈的換手率，重新發佈不清除；不回推。
- D6 `exclude_new_days`：實作，30 個交易日；兩個交易所的歷史都算（轉上市不算新股）；所有資產類別；0 為停用。
- D7 指標慣例：SMA 起始的 EMA（前 n−1 根為 null）、MACD 暖機期為 null（DIF 前 25 根、DEA 前 33 根）、Wilder RSI 與 ATR 起始、KD 從 50 起算；以指標定義版本強制一次全量重新發佈。布林 σ、年化、MACD 柱 ×2 留給第 5 階段。
- D8 策略條件：a) bottom_reversal 出場改為收盤創 20 日新低或由上往下跌破 MA20；b) 爆量改與前 5 日均量比；c) n_day_low_reversal 改為盤中低點跌破前 60 日低點且收盤收回其上；d) consolidation_breakout 的 `distance_from_low_60d` 低者優先、量縮預設 0.9；e)（修訂後）漲停鎖死的選股不降級、維持原分級，另加「漲停鎖死」標籤（原方案會讓 1,493 筆前 5 名中的 561 筆變成觀察）；f) 反轉策略的 `rsi_14`、`momentum_5d` 低者優先；g) pullback_to_support 要求收盤 ≥ MA20。
- D9 composite union：除以全部子策略的權重（沒選到的子策略計 0）；技術上延伸到 intersect。
- D10 流動性：成交額 ≥ 1 億元取代 200 萬股門檻；已存的 `min_volume` 覆寫仍會套用。
- D11 剛好 0 分是合法的最低分，只有 null 才判觀察。
- D12 已凍結的選股快照不回改；績效頁顯示策略邏輯變更日（第 5 階段的畫面）。
- D13 Lorentzian 候選窗口 N＝120 根有效 K 線。
- 技術選擇（規劃與複審時決定）：NaN 代表無法評分、±inf 拒絕；選股與回測同一個評分規則；同分依代號；過濾上下限遇缺值即排除；覆蓋為 0 的欄位在回測拒絕；原始 `rolling_*` 只留給自訂策略、內建策略禁用；暖機使種子權重低於 1e-4；單一的上市交易日數函式；META `scoring_directions`；指標定義版本；macd_golden 共用 MACD；composite 共用名次正規化；移除 `_5d` 排序欄位；前端用後端的 `turnover_rate`；factor_rank_research 用名次百分位；訊號標籤只用於顯示；快取列併入漲跌停旗標。之後的複審另外決定：composite 的子策略若是 `descending=False`，該 composite 載入失敗並列入 `load_errors`（不影響其他策略），只有透過已儲存的覆寫指定的子策略才會在 composite 執行時報錯（低者優先改用 `scoring_directions`）；`descending=False` 的回測依名次配置權重；換手率覆蓋以合格標的 50% 為門檻，回測另列覆蓋不足的交易所；選股與監控的覆蓋檢查由框架依 `required_coverage_fields` 執行；盤中以 MIS 估計的成交額只供流動性過濾，`vwap_bias`、`amount_ratio_5d`、`amihud_20d` 在估計列上為 null。

第 3、6 階段待辦：

1. 收盤集合競價實測：2026-10-08 沒錄到 13:25–13:30 的 MIS 樣本，預計 2026-10-12 錄製並核對試撮（`ts=1`）、13:30 結果第一次出現的時間與收盤同步是否等到每一檔（A13#1、A13#7）。錄製用的暫存腳本（不在倉庫內）已排定在 2026-10-12 執行，只讀 MIS、不寫本機資料。
2. 第 3、6 階段的程式已提交（`ac16ee5`，稽核文件 `b230f60`）。
3. D3 的 MIS 零股量（`ov` 欄位）是否併入盤中量，待 2026-10-12 的 MIS 錄製後再評估。
4. `2f691d5` 的 21:30 定稿門檻（依據是 TWSE 每日收盤檔約在 17:30 產出）：預計 2026-10-12 由暫存腳本在 17:15 與 22:30 各複製一次當日的 `data/taiwan/daily` 分區並逐檔比對開高低收量額（16:30 的寫入與 21:40 重抓後的差異），作為這個門檻的實測依據。這要靠當天 16:30／21:40 的排程寫入分區，而同一個排程在第 4 階段上線前會觸發全量重新發佈，兩件事要一起安排（見「第 4 階段上線（尚未執行）」）。
5. 已知限制：分鐘量的涵蓋率把第一個快照之前的量都算成未歸屬；若 MIS 不是一次公布所有收盤結果，收盤同步可能早於最後一檔結束；盤中為整股量，與官方全日量不同；09:05 探測只在當天還沒看過任何當日報價時才把輪詢降為每 5 分鐘，看過一筆後就恢復正常輪詢。

第 4 階段的實測影響（S10a，2026-10-10，在資料複本上量測，沒有連網，也沒有寫入本機 `data/`）：

前：HEAD `2f691d5` 的程式配第 4 階段之前的資料複本；後：04:14 的工作樹快照（R-G 之前）配由新程式重新發佈的同一份複本（股本檔另從本機唯讀複製，才有私募股數）。選股比較 21 支策略在 2026-09-09～10-08 的 20 個交易日；回測固定 2025-01-02～2026-09-30、部位模式 10 檔、隔日開盤進場。

- 全量重新發佈：每日排程呼叫的 `refresh_recent()` 偵測到指標定義版本不同，重寫 670／670 個分區、1,446,402 列，73.2 秒（HEAD 全量 `refresh()` 70.7 秒），記錄 `indicator_version = 2`；10-08 的 1,946 筆換手率全數保留，其中 266 筆因 D4 分母改變（中位數 ×1.24、最大 ×14.36）；第二次只重寫最近 5 個分區（19.4 秒）。策略快取重算 20.9 秒（HEAD 17.4 秒），20 支策略都沒有失敗。
- 選股（21 支 × 20 個交易日）：總數 15,375 → 15,314；剛好 0 分 8,405 → 11；null 分數 0 → 13（low_volatility_leader 中上市 30～60 個交易日、還沒有 `momentum_60d` 的標的）；ETF 3,325 → 3,201；上市未滿 30 個交易日 32 → 0；high_turnover_surge 有 19 個交易日被拒絕（換手率從 10-08 才有）。
- 分級：買進 1,031 → 1,360；HEAD 的 293 筆 `zero_score` 觀察變成 13 筆 `score_unavailable`；1,677 筆選股帶「漲停鎖死」標籤，前 5 名中 607／1,588 筆，最多的是 strong_open（80／95）、trend_breakout（76／100）、boll_breakout（74／100）。
- 回測年化：bullish_alignment 11.3% → −12.6%、low_volatility_leader 11.2% → 2.8%（HEAD 的正報酬是分數全為 0、ETF 依代號排前的假象，HEAD 的進場至少 92% 是 ETF）；pullback_to_support −7.1% → −24.0%；consolidation_breakout −5.1% → −14.4%；lorentzian_knn −19.6% → −26.3%（第一筆可分類的進場延到 2025-07-14）；n_day_low_reversal −0.5% → −9.4%；strong_open −36.2% → −39.2%（新股排除拿掉一筆 +55.7% 的進場）；pullback_resume 0.1% → 8.6%；oversold_reversal 0.4% → 2.6%；factor_rank_research 選股 5 → 400 筆、年化 −6.1% → −5.8%。ma_golden_cross、pullback_ma20_bounce、trend_breakout 的回測完全相同。每一項實質變化都能對應到上面的決策。
- 已發佈的指標：已存 340 根以上的標的只差種子衰減（10-08 的 EMA 最大相對差 2.7e-6）；較短的標的變化大（10-08：EMA 最大 1.7%、RSI 最大 7.22 點、ATR 最大 44%），並有 ema5 2 檔、ema20 6 檔、ema60 24 檔、macd_dea 13 檔、atr_14 4 檔改為 null。`signal_macd_golden` 在兩個抽查日（10-08、2025-06-30）都沒變。本機 `data/` 的 10-08 換手率已與新程式相同（開發後端 10-09 21:40 寫入），ema60 仍有 1,003 列是舊慣例。
- 耗時：run_all 每個日期 9.2 → 16.5 秒（共用歷史 122 → 468 個交易日，lorentzian 要 407 根）；監控後續每輪 9.0～11.4 秒（HEAD 8.6～9.7 秒），第一輪 13.9 秒；K 線 1 年熱快取 0.013～0.026 秒；21 支內建策略的回測 153 → 113 秒；單一策略 API 多 1～2 秒。
- R-G 收尾後以同一份資料重跑：選股、分級、失敗與 21 支回測都與 S10a 相同；新增的缺值欄位標示只出現在那 13 筆 null 分數列（都是 `momentum_60d`）。

第 4 階段上線（尚未執行，等使用者核准）：

本機 `data/` 目前的狀態（2026-10-10 唯讀核對）：

- enriched 670 個分區（2024-01-02～2026-10-08，433 MB），因子紀錄（10-08 16:36）沒有 `indicator_version`，所以新程式第一次的台股資料更新就會全量重寫全部分區。觸發點是 16:30／21:40 排程（週一到週五，休市日照常執行，下一次是 2026-10-12 16:30）、設定頁「更新到最新」或匯入歷史資料包。
- 開發後端仍在 127.0.0.1:3018 執行未提交的工作樹，改程式會自動重新載入，所以上面的觸發點現在就有效，而且沒有先備份。
- `data/user_data/strategy_cache.json` 是 2026-10-09 21:41 由開發中的程式寫的（S5 與 R-B～R-G 之前）；快取沒有新鮮度檢查，重算之前首頁會一直顯示它。
- 沒有自訂、AI 或 composite 策略，也沒有策略覆寫、監控規則或自訂訊號，本機不需要遷移使用者資料。

計畫的上線步驟（都還沒執行）：

1. 先停開發後端，或確認它在備份前不會觸發資料更新。
2. 備份 `data/kline_daily_enriched/`（670 個分區）、`data/.taiwan_enriched_factors.json`、`data/.matrix_generation_stock.json`、`data/user_data/strategy_cache.json`、`data/user_data/strategy_picks/`、`data/taiwan/share_capital.parquet`（10-09 21:40 已被改寫，更早的版本只有另外留存的備份才有）；`data/.backtest_matrix_cache` 可以重建，不需備份。
3. 刻意執行一次全量重新發佈（設定頁「更新到最新」，或 `refresh_recent()`／`refresh()`），確認因子紀錄有 `indicator_version: 2`、670 個分區都重寫、10-08 的換手率保留。
4. 重算策略快取（run-all 或重新啟動後的自動重算），檢查首頁卡片：`failures`、high_turnover_surge 在沒有換手率的日期被拒絕、「漲停鎖死」標籤。
5. 記下策略邏輯變更日（D12）＝第 3 步的日期，寫進本頁與 `docs/features.md`；績效頁顯示變更日留給第 5 階段。
6. 其他部署的升級須知已寫在 `docs/deployment.md`「2026-10 策略與選股更新（第 4 階段）的升級須知」。

2026-10-09 開發後端改寫本機資料的經過（事後唯讀核對檔案時間與選股紀錄）：

- 2026-10-09 21:40，開發後端的排程（10-09 休市，排程照常執行）以當時未提交的第 4 階段工作樹執行台股資料更新；不是 agent 執行的。
- 改寫的內容：`data/taiwan/share_capital.parquet` 連網重抓並加上私募股數（21:40:18）；enriched 2026-10-02～10-08 的 5 個分區以部分窗口重新發佈（21:40:49–50），10-08 的換手率因此已是 D4 的分母，指標仍是舊慣例（S5 之前的程式），因子紀錄與 `.matrix_generation_stock.json` 沒有改（仍是 10-08 16:36）；`data/user_data/strategy_cache.json` 重算（21:41:22）；10-08 的選股紀錄中，boll_breakout、strong_open、volume_price_surge 3 支策略在 21:41 被重新記錄（run `0f5499b`），其餘 17 支的最後紀錄仍是 10-09 16:33（run `0cd1d52`）。這 3 支的策略檔沒有改、指紋相同，依快照規則可以重新記錄；其餘 17 支的指紋已改變，紀錄不會被覆寫。
- 沒有資料遺失；但 10-08 這 3 支策略的選股紀錄反映的是開發中的程式（新股排除、null 分數、歷史長度等引擎規則）。
- 使用者在 22:55 決定在上線與提交之前停用開發後端；當時核對已沒有後端行程，但開發後端在 22:55:10 又被啟動。2026-10-10 02:25 核對時，它只寫了 `backend.log`、`taiwan/holidays/2026.json`（00:01 的休市表排程）與 `user_data/preferences.json`；本次文件更新再看檔案時間，enriched 分區、因子紀錄、策略快取與 10-08 的選股紀錄仍是上面列的時間。

第 2 階段上線紀錄（2026-10-07，唯讀核對過）：

原定的步驟是先看 dry-run、確認 00793B、00847B、8183 的下市，再以 CLI 手動做第一次刷新。實際上新程式啟動後自動路徑就已啟用，dry-run 沒有另外執行，由下面的正式刷新取代；下市核對則在刷新之後補做。

1. 16:30 排程的主檔刷新在上市公司清單（`t187ap03_L`）回應後、送出任何 TPEx 請求前就以 ReadTimeout 失敗（16:30:49，逾時的是上市 ETF 清單 `t187ap47_L`）；日K則因 TPEx dailyQuotes 中斷連線而失敗（16:31:57），策略快取略過，沒有寫入任何不完整的資料。
2. 18:43 使用者按設定頁「更新」：主檔刷新成功（`last_success` 2026-10-07T18:43:28+08:00）。新增 00992B、6604、7812、7856，下市 00793B、00847B、8183（`delisted_at` 2026-10-07），重新分類 388 檔；TPEx ETF 型態查到 117／119 檔，`styles_complete` 為 true。結果與 S6 的 dry-run 一致。
3. 兩次「更新」重疊執行：18:43:27 的第一次啟動主檔刷新（`last_success` 18:43:28 是刷新開始時間），18:43:51 的第二次因刷新進行中而略過主檔，先以舊主檔全量重新發佈 enriched（18:44–18:45）並重算策略快取（18:45:14）；第一次的刷新在 18:46:46 完成後，因 22 檔有歷史的標的漲跌幅規則改變（例如 00400A）再全量重新發佈一次（18:48–18:49），18:49:51 重算策略快取（20 支策略）。每次全量重新發佈約 1 分鐘。
4. 最後一次策略快取重算在 18:49:51，用的是新主檔。
5. `kline_daily` 的 2026-09-16、09-17、09-18、09-22 四個分區核對雜湊後移到 `data/_quarantine/kline_daily-2026-09/`，`data/kline_daily` 已清空（A01#5）。
6. 上線後唯讀核對：主檔 2,381 列、單一 `updated_at`、10 檔 TDR、`listing_date` 全為 ISO；上市 ETF 未確認分類 0 檔，上櫃 1 檔（00980T）。7812、7856 有 9/22 起每個已存交易日的日K，6604 有 10/06–10/07，00992B 上市日為 2026-10-08。enriched 669 個分區、同一個 82 欄 schema，`limit_rules` 記錄 2,349 檔；TDR 換手率為 null；00877、00635U 的 `ref_limit_up/down` 為 null。
7. 下市核對：8183（與 6191 股份轉換，9/22 最後交易，10/1 終止上櫃）與 00793B（規模低於門檻，已公告下市）已確認；00847B 沒有找到下市公告，最後一筆已存日K是 10/02，之後不在官方清單，暫列下市並保留歷史，若重新出現在官方清單會自動改回 active。

每完成一個階段，就更新本節的表格、[recheck-2026-10-06.md](recheck-2026-10-06.md) 對應的列，以及下一節的待修正清單。

## 目前待修正項目

除了最前面的「立即處理」，其餘依嚴重度分組：先 P1，再依「會不會給出錯誤的金融數字或判斷」排 P2，最後是 P3 與測試補強。執行順序以上一節「修正順序規劃」為準。標第 4 階段的行號對目前工作樹，標第 3 階段的對 `ac16ee5`，標第 2 階段的行號對 `b3a8007`，標第 1 階段的行號對第 1 階段完成時的工作樹，其餘對 `6582e9e`；完整證據見 [recheck-2026-10-06.md](recheck-2026-10-06.md)。

### 立即處理：讓既有測試回到全綠（2026-10-06 已完成）

- 已完成：測試不再送出 `stamp_tax_pct`，mining 的防護未放寬，`tests/backtest` 全目錄 `308 passed`。原問題：`backend/tests/backtest/test_worker_process.py` 的 5 個測試仍送出 `"stamp_tax_pct": 0.0`（`:257`、`:311`、`:369`、`:572`；`:572` 是 2 個測試共用的 helper），被 `backend/app/backtest/mining_runtime.py:902-903` 拒絕。應改成不送這個欄位，讓防護保持不變；之後的驗收命令要涵蓋整個 `tests/backtest`。

### P1：會使金融結果或核心判定不可信

第 2 階段之後，27 條 P1 全部已修正；第 6 階段為其中 6 條補上直接測試後，27 條都有直接測試。最後一條 A12#0 在第 2 階段改為依交易所判定產業情報的品質（`backend/app/taiwan/industry_intelligence.py:270-289`）。

第 1 階段已修正的 P1：回測漲跌停鎖死（A11#1、A05#0）、非策略回測路徑的 A 股成本（A11#0 殘留，舊路徑已刪除）、回測以復權價計算股數與手續費（A11#2 殘留），以及除權息日分時漲跌停線（G05#0 殘留）。

### P2：明確功能、分類或顯示錯誤

1. **盤中增量路徑另有一套遞推**（RC09，10 條：A02#5、A04#11 在第 2 階段修正，其餘 8 條在第 3 階段讓台股盤中列改走與盤後相同的批次管線後修正，台股部署範圍）；非台股標的仍用舊遞推（`backend/app/repository.py:766` 等，行號對 `6582e9e`），第 4 階段的指標慣例（D7）也沒有套到非台股盤中路徑的 EMA12／26 與 RSI 狀態（`backend/app/repository.py:937-970`，第 4 階段工作樹）。
2. **盤中量能口徑與時間折算**（RC10）：開盤量比折算與前端「量比N」已在第 3 階段修正；盤中量仍是 MIS 整股量、盤中成交額顯示「—」，依決策 D3 保留並標示（A04#5、A13#3、A06#3、A06#11 判為部分修正）。
3. **交易時段與休市日曆**（RC11）：第 3 階段已修正；13:25–13:30 的收盤集合競價（A13#1、A13#7）待 2026-10-12 實測。
4. **缺值當成 0 分或排最前**（RC13，P2 5 條，另 1 條 P3）：第 4 階段已修正。缺評分輸入的候選分數為 null、保留、排最後並標出缺值欄位，分級為觀察（`score_unavailable`），0 分是合法的最低分（`backend/app/backtest/matrix.py:4134-4135`、`backend/app/strategy/engine.py:1428-1437`、`backend/app/services/pick_grades.py:124-125`）；選股器空值排最後（`backend/app/taiwan/screener.py:617`）；關鍵價位不再把 null 換手率當 0（`backend/app/indicators/levels.py:108-113`），但要等換手率逐日累積到整段視窗，才會用到衰減的籌碼模型。`backend/tests/backtest/test_matrix_strategy.py:880-882` 的預期值已改為 NaN。
5. **策略矩陣的滾動窗口與暖機**（RC24，3 條；RC25 的 P2，4 條）：第 4 階段已修正（程式）。內建策略改用只看有效 K 線的 `valid_rolling_*`；D7 的指標慣例；`exclude_new_days` 以 30 個交易日實作（`backend/app/strategy/listing_age.py:27-96`）；macd_golden 改用共用 MACD 並宣告 190 根歷史（`backend/app/strategy/builtin/macd_golden.py:53-64`）；lorentzian 的候選池不再隨載入的歷史改變。已發佈的指標值要等上線時全量重新發佈。殘留：A09#7（P2，部分修正）lorentzian 與 TradingView 原版的設計差異只以文件說明，策略 META 的說明（`backend/app/strategy/builtin/lorentzian_knn.py:53`）沒有提到。
6. **個別策略條件與 composite 合併**（RC23 的 P2，2 條；RC28，1 條 P2）：第 4 階段已修正。bottom_reversal 出場改為收盤由上往下跌破 MA20 或創 20 日新低（`backend/app/strategy/builtin/bottom_reversal.py:162-167`）；factor_rank_research 改用名次百分位（`factor_rank_research.py:197-237`）；composite 選股與回測共用 `rank_normalize`（`backend/app/strategy/composite.py:37-60`）。
7. **證券主檔與商品分類**（RC14、RC27）：第 2 階段已修正，主檔刷新與 enriched 全量重新發佈已於 2026-10-07 上線。
8. **分鐘資料與分時圖**（RC18 已在第 3 階段修正；RC16，1 條 P2）：1e9 被當成 1 億（`frontend/src/components/EChartsIntraday.tsx:33-34`，行號對 `6582e9e`）。
9. **大盤指數與研究脈絡**（RC15，1 條 P2）：詳細頁的大盤基準卡仍以空陣列解析（`backend/app/taiwan/detail_service.py:626-629`），永遠顯示「--」。加權指數日線現在每天都會回補（`backend/app/jobs/daily_pipeline.py:129-211`），所以可以直接接上。
10. **名稱與實際計算不符**（RC17 的 P2，3 條）：SHORT_SURGE 的「減少」仍標成「融券激增」（`frontend/src/pages/TaiwanScreener.tsx:971`）；「跌停翻紅」不要求收盤高於昨收（`frontend/src/lib/signals.ts:160-162`）；「價量背離」命名。
11. **官方資料的快取與容錯**（RC20，A13#2 已在第 3 階段修正）：基本面與 ETF 資料的抓取仍沒有快取（見 G02）。
12. **其他 P2**：
    - 關鍵價位的盤中樞軸點漂移與缺口回補判定（`backend/app/indicators/levels.py:172-179`、`:441-450`）。
    - 詳細頁即時路徑的券資比為空（`backend/app/taiwan/detail_service.py:535`）。
    - 異常診斷在融資融券 20 日中位數為 0 時不評估（`backend/app/taiwan/abnormal_diagnostics.py:510`）。資料品質只看有無資料的問題（G01#1）已在第 2 階段修正。

### P3 與測試補強

- **P3 未修正 43 條、部分修正 4 條**（第 4 階段之前是 66 條、3 條），集中在：
  - RC16 顯示格式：萬億、股／張、MACD 柱（A02#4，strict xfail）、簡體標籤。
  - RC21 關鍵價位細節：Keltner、取整、聚合、舊欄位名。
  - RC22 A 股遺留：regime、財務欄位（`kline_daily` 的讀取端與殘留分區已在第 2 階段處理）。回測基準已在第 1 階段改用加權指數。
  - RC29：只剩 A06#8，ETF 的換手率與市值維持 null，「不適用」的顯示延到第 5 階段（決策 D4）。
  - RC30 布林標準差（A08#6 以 strict xfail 釘住）。
  - RC31：`annual_vol_20d` 仍以 252 日年化（A04#8、A09#10，strict xfail）；回測統計與 walk-forward 已在第 1 階段修正。
  - RC32 數值邊界。
  - RC33 缺列語意。
  - RC34 資料標示：AI 證據少大盤脈絡的時段，在 `fea6532` 後延長到隔日。
  - RC25、RC28、RC35、RC36 已在第 4 階段關閉。
  - 以及 A05#14、A06#11、A04#5 的殘留，和 A08#9（漲停鎖死的選股只加標籤、排名不變，依決策 D8e 修訂版）。
  - 逐條見 [recheck-2026-10-06.md](recheck-2026-10-06.md)。
- **補測試**：第 6 階段已為 A02#0、A03#0、A04#0、A08#2、A09#0、A10#0、G04#2、G04#4、A02#7、A04#9 補上直接測試；第 4 階段改判的 36 條已修正都有直接測試，沒有「已修正（無測試）」的條目。
- **回測頁的口徑揭露**：年化與 Sharpe 口徑、無風險利率、滑價、原始成交價與前復權損益、加權指數不含息、最低手續費預設（整股 20 元、零股 1 元），都已在第 0、1 階段寫進頁面說明。

### 第 1 階段留下的已知缺口

- （第 3 階段已處理）台股部署的盤中路徑原本不計算漲跌停訊號與連板數；現在台股盤中列改走與盤後相同的管線，訊號與連板數一致（`backend/tests/test_stage6_intraday_consistency.py:285`）。
- 新上市規則以「同代號在上市日前已有本機日K」判斷轉上市，代號被重新使用時會把新公司誤判為轉上市而照 10%。
- （第 2 階段已處理）TDR 原本以「91 開頭的 6 位數代號，或名稱以 -DR 結尾」判斷；現在改用 `security_class`，產業代碼 91 也算，名稱不帶 -DR 的 4 位數 TDR 不會再套用新上市規則。
- 新上市期間依本機已存的交易日計算（`listing_sessions` 有 60 秒快取），其他行程寫入新交易日後最多延遲 60 秒才生效；日K漏存時期間會往後延長，上市日早於本機第一個交易日時照 10%。
- 檔位表的批次路徑沒有逐列帶交易日，千金股升降單位新制（預計 2027 年 7 月）加入表格前要先補上。
- （第 2 階段已處理）enriched 原本只看公司行動的指紋；現在窗口外標的的漲跌幅規則改變也會全量重建；2026-10-07 上線時已記下基準並全量重新發佈。
- （第 2 階段已處理）bootstrap 換檔改為不覆蓋較新的本機主檔，並立即重新載入；主檔檔案變動時，程式內的主檔與漲跌幅快取也會重新載入。
- （第 4 階段已處理回測路徑）回測的 polars 面板路徑（選股與即時監控也共用）缺漲跌停訊號欄時，原本會靜默當成沒有鎖死（`backend/app/backtest/matrix.py:2892-2902`，行號對第 1 階段）。現在台股 Polars 回測讀已發佈的 `signal_limit_*`，沒有發佈值的列以台股發佈規則補算（`backend/app/backtest/engine.py:69`、`:862`），`read_enriched_window` 的重算列也帶旗標；最後防線 `_bool_matrix` 對缺欄仍當成未鎖死（`backend/app/backtest/matrix.py:2930-2940`，第 4 階段工作樹）。
- 報價面板與詳細頁在除權息日仍用 MIS 的昨收算漲跌停，沒有改用官方參考價。
- 配股增加的股數不追蹤（公司行動表沒有配股比例），賣出費稅略為低估，淨值不受影響；減資退還股款時，出場股數是近似值。
- walk-forward 的伺服器 guard 只檢查單折長度，限制不到共用矩陣涵蓋的整段期間（guard 預設關閉）。
- mining 的 Sharpe 不套 60 個交易日的門檻，無風險利率固定為 0。
- 回測基準是加權價格指數、不含息，策略淨值含息，超額報酬會高估約一年的殖利率。

### 第 2 階段的已知缺口

- 第一次主檔快照之前就下市的標的不在主檔裡；早於該快照的目標日，市場統計與產業情報的母體可能少算（沒有 `known_since`）。
- 公司行動事件日的 `ref_limit_up/down` 若被官方更正，不在 enriched 的指紋內，不會自動全量重建。
- 上櫃 ETF 的主被動型態只能靠 TPEx ETF/detail 逐檔查詢；00838B、00992B 這類查不到型態的標的，仍以一般 ETF 稅率 0.1% 計算，不推定為被動債券 ETF 的免稅。00980T 的成分範圍仍未確認，漲跌幅規則未知。2026-10-07 刷新時 TPEx ETF 型態查到 117／119 檔。
- 00847B 沒有找到下市公告：最後一筆已存日K是 10/02，之後不在官方清單，目前暫列下市並保留歷史；若重新出現在官方清單，下一次刷新會自動改回 active。
- TDR 證交稅 0.1% 依[財政部稅務入口網問答](https://www.etax.nat.gov.tw/etwmain/tax-info/understanding/tax-q-and-a/national/securities-transaction-tax/filing/LeJ1WWV)（台財稅第811672688號函）；TDR 當沖也適用 0.1%，是我們對證券交易稅條例第 2 條之 2（只適用上市或上櫃股票）的解讀，尚未另行查證。
- `data_integrity` 只回報、不修復：開機後 30 秒寫一次 log。每日更新的 20 個分區重抓窗口只重抓缺一個交易所（one_exchange）與 provisional 的分區；整天缺漏（missing）要手動回補，除非剛好落在該次的抓取範圍內。
- `--symbols` 只補缺列、不覆寫已存列；要更正已存的上櫃列，仍要走全市場快照路徑。
- 新上市回補只重抓上市日到第一筆已存日K之前的日子；之後某一天重抓失敗，和停牌分不出來，不會自動重試，要用 `--symbols` 補。
- 啟動後到共用管線的歷史準備好之前（第 3 階段起），台股當日沒有盤中 enriched 列，準備好後恢復。
- `/code/validate` 不檢查策略的 asset_types；類別不符的標的不列在回測統計的 `excluded_symbols`。證券類別過濾只套用在 `.TWSE`／`.TPEX` 標的；主檔不可用時，策略相關端點回 503。
- 以下既有測試在 HEAD 就會嘗試連外：`test_taiwan_stock_detail_service.py`（約 333 次）、`test_taiwan_ai_research.py`（約 62 次）、`test_taiwan_stock_detail_api.py`（約 144 次）、`test_taiwan_backtest.py`（約 11 次）；另有測試在 `DATA_DIR` 沒有主檔時會下載主檔，`test_taiwan_ai_research.py`、`test_taiwan_ai_research_validation.py` 各有 1 個案例與 `test_taiwan_backtest.py::TestGolden2330Backtest` 需要真實資料。留給第 6 階段的測試整理。

### 第 4 階段的已知缺口與延後項目

行號對目前工作樹。寫給策略作者的限制，同時寫進了 AI 策略指南（`backend/app/strategy/prompts/`）；給其他部署的升級須知在 `docs/deployment.md`。

延到第 5 階段（畫面與已決定延後的口徑）：

- A06#8：ETF 的換手率與市值仍為 null，畫面改顯示「不適用」留給第 5 階段（決策 D4）；ETF 市值沒有在任何決策中處理。
- D7 明定留給第 5 階段的指標口徑：布林 σ（RC30：A05#10、A08#6、A09#8、G04#6）、`annual_vol_20d` 以 252 日年化（A04#8、A09#10）、MACD 柱 ×2（A02#4）；其中 A02#4、A04#8／A09#10、A08#6 以 strict xfail 釘住。
- 漲跌都是 0 時 RSI 給 0（`backend/app/indicators/pipeline.py:333`），第 6 階段的參考值是 100；收盤為 null、NaN 或 inf 的現存列會讓 pipeline 與 matrix 的結果不同（已發佈的約 145 萬列中沒有這種列）。
- 回測頁交易明細的 `exit_reason` 直接顯示原始訊號 ID（`frontend/src/pages/StrategyBacktest.tsx:300`）。
- 無法載入的策略（`load_errors`）沒有清單畫面；`GET /api/strategies/{id}/source` 已會回傳 `load_failed`、`load_error`，供之後的編輯器使用。
- composite 的編輯器與基本過濾：自己沒有 `basic_filter` 的 composite 會對子策略套用引擎預設過濾（股價 ≥ 3、成交額 ≥ 2000 萬、TWSE／TPEX、30 個交易日的新股排除，`backend/app/strategy/engine.py:45-58`），composite 的過濾會整個取代子策略的；`/api/strategies/composite/save` 不能設定過濾（`backend/app/api/strategy.py:305-312`），畫面也沒有顯示。
- 回測頁的欄位覆蓋提示已改用欄位顯示名；`cnSignal` 沒有對照的欄位仍會顯示原始 ID（`frontend/src/lib/signals.ts:232`）。目前需要覆蓋檢查的欄位只有 `turnover_rate`，有對照。
- 績效頁標示策略邏輯變更日（D12）；上線時先把日期記在本頁與 `docs/features.md`。

未排入階段的既有問題與限制（backlog）：

- 測試衛生：`test_mining_manager` 有既有競態（`backend/app/services/mining_manager.py` 先寫狀態、後寫最後的事件），HEAD 也會間歇失敗；`tests/test_taiwan_research_context.py` 的技術脈絡測試讓 pytest 約佔 19～22 GB RSS（HEAD 也一樣），全量回歸要另開行程；第 4 階段的工作樹上，pytest 印出結果後要再約 22～85 秒才結束（HEAD 約 3 秒），時間花在直譯器收尾，不影響結果，原因未查明。
- 非台股盤中路徑的 EMA12／26 與 RSI 狀態仍是舊起始方式、沒有暖機門檻（`backend/app/repository.py:937-970`），只影響非台股資料來源。
- 手動放進策略目錄、檔名與 Python 模組同名的使用者策略檔（例如 `json.py`）會在整個行程取代該模組（策略以檔名當模組名載入，`backend/app/strategy/engine.py:463-469`）；透過 API 存的策略 ID 不會碰到。
- `python_history_legacy` 策略：選股時收到目標日加前 `LOOKBACK_DAYS` 個交易日，回測時 `filter_history` 收到整段面板（`backend/app/backtest/strategy.py:2304-2307`），只能用逐檔的位移與滾動運算，整表彙總（例如只取最後一天）會讓回測出錯。
- 只有基本欄位、沒有已發佈指標的列（例如強制合併的盤中列）：若是該標的在載入面板中的第一列（面板起點，或停牌超過載入範圍後復牌），補算的漲跌停旗標會是 False（實測起始日全為這種列時，有 62 列與發佈值不同）；只要有這種列，該標的的所有列都會重算漲跌停訊號，Polars 回測與歷史快取會多花幾秒（例如 Polars 回測 5.8～6.6 秒變 12.5～13.7 秒）。
- 換手率凍結的限制：一天的換手率固定在當天最後一次同日發佈，事後修正成交量不會更新；當天第一次存檔時缺的標的整天為 null；整個交易所缺資料時，該日覆蓋率可能低於 50%，回測會一直拒絕；重新發佈時暫時讀檔失敗，該日的換手率會遺失；刪除最新分區時，前一天會用目前的股數重算。
- `exclude_st` 在台股 Polars 回測會生效（回測帶有主檔名稱），選股時沒有名稱欄、不會生效；目前主檔 2,381 檔中沒有符合的名稱。
- 加權指數長區間 K 線：539 天的暖機會觸發早於本機指數資料的月份同步回補（每月 20 秒逾時、失敗後 600 秒才重試），網路不通時每 10 分鐘可能卡約 80 秒；365 天的圖會嘗試回補 2024-04～07 一次。
- 其他已記錄的限制：`descending=False` 的回測依名次配置權重，加上單檔上限只投入約 76～83% 資金（高者優先時 90～95%），`order_by` 欄位配 `descending=False` 用等權；個股詳情 API 合併換手率多約 120～160 ms；選股端沒有逐交易所的覆蓋率警告（回測有）；high_turnover_surge 的 2% 只依 2026-10-08 一天校準；lorentzian 要約 407 個交易日的歷史，新上市股一年半以上才有訊號；MIS 不給成交額，用到 `vwap_bias`、`amount_ratio_5d`、`amihud_20d` 的 mining 策略要到收盤後才有訊號；選股紀錄的指紋不反映引擎層規則的變更，所以上線要記錄邏輯變更日；選股時評分欄位覆蓋低只會讓候選變成 null 分數（觀察），回測在評分欄位覆蓋低於 50% 時拒絕；只有 `order_by`、沒有評分權重的自訂策略，缺值排最後但依名次分級；回測排除漲跌幅規則未確認的標的，選股不排除；`run_preset` 把訊息含 "unknown strategy" 的 ValueError 都轉成 404；前端 `frontend/src/lib/stock-table.ts:22` 的訊號徽章清單仍列出後端不存在的 `signal_broken_board_recovery`，這個徽章永遠不會出現（程式未改，只記錄）。

### 已知安全限制，不視為完成度漏洞

- 歷史日期的母體以 `listing_date` 與 `delisted_at` 推算（第 2 階段起，不再要求主檔時點早於目標日）。第一次主檔快照之前就下市的標的不在主檔裡，這段期間的母體可能少算，見「第 2 階段的已知缺口」。
- 當沖資格必須由呼叫端提供白名單；預設為空，不推測個股資格。回測頁目前沒有開放這個欄位。
- ETF 主動／被動型態：上市 ETF 取自官方基金類型，上櫃 ETF 在主檔刷新時查 TPEx ETF/detail 的「ETF類別」。查不到型態時，以一般 ETF 稅率處理，不猜成債券 ETF 的優惠稅率。2026-10-07 刷新後，上櫃 ETF 有 110 檔被動、7 檔主動、2 檔查不到型態。
- 被動債券 ETF 免徵證交稅只套用到 2017-01-01 至 2026-12-31（證券交易稅條例第 2 條之 1）；2027 年起依現行法改課 ETF 稅率 0.1%，不預測尚未生效的延長修法。股票當沖減半到 2027-12-31（第 2 條之 2），之後依現行法回到 0.3%。
- 同一次回測若混合台股與非台股標的，會明確拒絕。
- mining 不接受全市場單一證交稅率覆蓋；台股稅率必須依標的類型決定。策略、優化與 walk-forward 在第 1 階段也改為拒絕。
- 回測遇到漲跌幅規則未知的台股（主檔查無、ETF 分類未確認，或一般股缺 `listing_date`），一律排除並列出原因，不猜成 10%。

## 本輪驗證與限制

2026-10-06 的重核由 6 個獨立 agent 依根因分組進行，以現行程式碼為準，不採信 commit message。第 1 階段後的判定同樣以工作樹的程式碼與下表的測試結果為準。實際執行的測試如下，後端都在 `backend/` 以 `%TEMP%` 下新建的隔離 `DATA_DIR` 執行：

| 範圍 | 命令 | 結果 |
|---|---|---|
| 回測全目錄 | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\backtest -q` | 重核時 `5 failed, 303 passed, 1 warning in 146.75s`；第 0 階段修正後 `308 passed, 6 warnings in 170.91s`；S8 之前 `449 passed, 6 warnings in 22.00s`；第 1 階段全部完成後 `465 passed, 6 warnings in 36.95s`（`DATA_DIR` 內都複製了 `data\taiwan\security_master.parquet`） |
| RC05／RSI 暖機 | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_rsi_warmup.py tests\test_taiwan_screener.py tests\test_taiwan_watchlist_enrichment.py tests\test_taiwan_research_context.py` | `78 passed in 184.00s` |
| 第 1 階段的其他後端測試（S8 之前） | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_taiwan_limit_streak.py tests\test_kline_minute_price_limit_taiwan.py tests\test_intraday_quotes_price_limit.py tests\test_taiwan_market_rules.py tests\test_price_limits.py tests\test_taiwan_screener.py tests\test_taiwan_stock_detail_service.py tests\test_taiwan_research_context.py tests\test_taiwan_backtest.py` | `262 passed, 5 warnings in 139.59s` |
| 前端回測頁 | 在 `frontend/` 執行 `pnpm test -- src/pages/StrategyBacktest.test.tsx` | 重核時 `14 passed` |
| 第 0 階段前端 | 在 `frontend/` 執行 `pnpm exec vitest run src/pages/StrategyBacktest.test.tsx src/pages/TaiwanScreener.test.tsx`，再執行 `pnpm build` | `67 passed`；build 退出碼 0，只有既有的 dynamic-import 與 chunk-size 警告 |
| 第 1 階段前端：回測頁與選股頁 | 在 `frontend/` 執行 `pnpm exec vitest run --no-cache src/pages/StrategyBacktest.test.tsx src/pages/TaiwanScreener.test.tsx` | S8 之前 `80 passed`；第 1 階段全部完成後 `84 passed` |
| 第 1 階段前端建置（S8 之前） | 在 `frontend/` 執行 `pnpm build` | 結束碼 0（由實作者執行，本次文件同步沒有重跑） |
| 第 1 階段前端：分時圖、報價面板、詳細頁（S8 之前） | 在 `frontend/` 執行 `pnpm exec vitest run --no-cache src/components/EChartsIntraday.test.tsx src/components/monitor/TaiwanQuotePanel.test.tsx src/components/StockPanel.test.tsx src/pages/TaiwanStockDetail.test.tsx` | `28 passed` |
| 第 1 階段前端型別檢查 | 在 `frontend/` 執行 `pnpm exec tsc -p tsconfig.json --noEmit` | 退出碼 0（S8 前後各執行一次） |
| S8 驗收組與相關後端測試 | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_taiwan_limit_ipo.py tests\test_taiwan_market_rules.py tests\test_taiwan_limit_streak.py tests\test_price_limits.py tests\test_taiwan_screener.py tests\test_kline_minute_price_limit_taiwan.py tests\test_intraday_quotes_price_limit.py tests\test_enriched_full_rebuild.py tests\test_taiwan_reference_prices.py tests\test_taiwan_research_context.py tests\test_taiwan_stock_detail_service.py tests\test_taiwan_market_intelligence.py tests\test_taiwan_backtest.py tests\test_mining_candidates.py` | `398 passed, 27 warnings in 205.74s` |
| enriched 重新發佈結果（唯讀核對） | 讀 `data/kline_daily_enriched`，與發佈前的備份（`~\stock-analyze-backups\enriched-before-stage1-20261007-002813`）逐列比對 | 668 個分區、1,441,762 列；2454.TWSE 在 2026-09-01 的連板數為 1，6225.TWSE 在 2026-09-23 的連跌數為 2；訊號為真但連板或連跌數為 0 的列為 0；`signal_limit_up` 改變 43 列、`signal_limit_down` 改變 21 列；`close`、`raw_close` 沒有任何改變 |
| 第 2 階段新測試與受影響模組（`no_network` 是本次驗證用的暫存 pytest 外掛，不在倉庫內，擋下並統計非本機連線） | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -p no_network -q tests\test_stage2_*.py`（13 個檔）加上 27 個受影響模組的既有測試檔（清單見 [recheck-2026-10-06.md](recheck-2026-10-06.md) 文末） | `852 passed, 4 warnings in 91.10s`；收尾修正（低風險項）後重跑 `866 passed, 4 warnings in 124.20s`；兩次外部連線嘗試都是 0 次 |
| 第 2 階段前端 | 在 `frontend/` 執行 `pnpm exec vitest run --no-cache src/pages/TaiwanScreener.test.tsx src/pages/Dashboard.test.tsx src/pages/TaiwanStockDetail.test.tsx`，再執行 `pnpm exec tsc -p tsconfig.json --noEmit` | `111 passed`（3 個檔案）；tsc 退出碼 0 |
| 第 2 階段本機資料（唯讀核對） | 讀 `data/taiwan/security_master.parquet`、`data/.taiwan_enriched_factors.json`、`data/kline_daily_enriched` 的 parquet footer、`data/kline_daily` 目錄 | 主檔最新 `updated_at` 為 2026-09-17、2,377 列，沒有刷新紀錄；7812 不在主檔，00793B、00847B、8183 仍是 active；上市 ETF 55 檔未確認分類；enriched 668 個分區都是同一個 82 欄 schema，因子紀錄沒有 `limit_rules`；`kline_daily` 仍有 4 個殘留分區（上線前的狀態） |
| 第 2 階段上線後（唯讀核對） | 讀 `data/taiwan/security_master.refresh.json`、主檔、`data/.taiwan_enriched_factors.json`、`data/kline_daily_enriched` 分區數、`data/kline_daily` 與 `data/_quarantine/kline_daily-2026-09/` | 結果見「修正順序規劃」的第 2 階段上線紀錄第 6 項；`data/kline_daily` 為空 |
| 第 3、6 階段後端（`no_network` 暫存外掛擋下非本機連線） | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -p no_network -q -rxX tests\test_stage3_*.py tests\test_stage6_*.py` 加上 7 個受影響的既有測試檔（清單見 [recheck-2026-10-06.md](recheck-2026-10-06.md) 文末） | `629 passed, 29 xfailed, 48 warnings in 249.20s`；29 個 strict xfail 都對應仍未修正的 finding；外部連線嘗試 0 次 |
| 第 3 階段前端 | 在 `frontend/` 執行 8 個受影響的 vitest 檔（清單見 recheck 文末），再執行 `pnpm exec tsc -p tsconfig.json --noEmit` | `43 passed`；tsc 退出碼 0 |
| 第 4 階段全量回歸（S10b：2026-10-10 04:12 的工作樹快照，R-G 之前；HEAD `2f691d5` 同時跑一次對照） | `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q -p no_network -rfEx`（全部後端測試；`tests\test_taiwan_research_context.py` 因記憶體用量另開行程） | 快照 `4276 passed, 15 failed, 1 error, 7 xfailed, 2 skipped`；HEAD `3662 passed, 17 failed, 1 error, 29 xfailed, 2 skipped`；`test_taiwan_research_context.py` 兩邊都是 `15 passed`。快照的失敗與錯誤和 HEAD 的基線逐條相同（HEAD 多的 2 個是 `test_mining_manager` 的既有競態）；22 個 strict xfail 轉為通過；609 個新測試全部通過。明細見 [recheck-2026-10-06.md](recheck-2026-10-06.md) 文末 |
| 第 4 階段前端（S10b） | 在 `frontend/` 執行 `pnpm exec vitest run --no-cache`、`pnpm exec tsc -p tsconfig.json --noEmit`、`pnpm exec vite build`（輸出到暫存目錄）、`pnpm exec eslint src` | `473 passed`（52 個檔）；tsc 與 build 退出碼 0；eslint 0 errors（19 個 warnings 與 HEAD 相同） |
| 第 4 階段 R-G 收尾修正後的目標回歸 | 191 個測試檔（全部 `test_stage4_*`、`tests\backtest`、`test_stage6_*` 與受影響模組的既有測試，不含 `test_taiwan_research_context.py`）；前端同上 | `2760 passed, 5 failed, 1 error, 7 xfailed`；其中 `test_monitor_localization` 的 1 個失敗是 R-G 新增欄位標籤造成的，補上後重跑通過，其餘失敗與錯誤都是基線；前端 `477 passed`（52 個檔），tsc 與 build 退出碼 0，eslint 0 errors |
| 第 4 階段前後對照（S10a，資料複本） | HEAD 與工作樹快照各跑 21 支策略 × 20 個交易日的選股與固定區間回測，並在複本上執行全量重新發佈與策略快取重算 | 見「修正順序規劃」的「第 4 階段的實測影響」 |
| 第 4 階段最終全量回歸（R-G 之後的工作樹快照） | 同第 4 階段全量回歸（S10b）的命令；前端同 S10b | `4290 passed, 17 failed, 1 error, 7 xfailed, 2 skipped`；`test_taiwan_research_context.py` 另開行程 `15 passed`。17 個失敗是 15 個基線加上 `test_mining_manager` 的 2 個既有競態（HEAD 上同樣失敗，`mining_manager.py` 未改動；隔離重跑 3 次時有過有不過），新失敗 0 個；與 HEAD 相比新增 625 個測試全部通過，22 個 strict xfail 轉為通過。前端 `477 passed`（52 個檔），tsc 與 build 退出碼 0，eslint 0 errors（19 個 warnings 與 HEAD 相同） |

限制：
- 第 4 階段的第一次全量回歸（S10b）是對 R-G 之前的快照執行；R-G 收尾修正之後先跑 191 個測試檔的目標回歸，再對 2026-10-10 05:50 的工作樹快照跑一次全量回歸（上表最後一列）。快照之後改過的檔案都不改程式行為：兩個 AI 提示檔的範例訊號（快照加上這兩檔重跑 `test_ai_generator_prompt.py`、`test_stage2_tdr_consumers.py`，`32 passed`），以及 10 個 `backend/app` 檔只改註解與 docstring：先把 18 段註解譯成英文，再修正 `indicators/pipeline.py`、`strategy/engine.py`、`indicators/levels.py` 的 docstring 與註解內容。這 10 個檔忽略 docstring 後的 AST 與快照相同；翻譯後重跑 45 個 `test_stage4_*` 測試檔為 `612 passed`，之後 3 個檔的 docstring 與註解修正同樣以 AST 比對確認，並在修正後的工作樹重跑 `tests\test_stage4_*.py`、`tests\backtest\test_stage4_*.py`（45 個檔）加上 `test_monitor_localization.py`、`test_rsi_warmup.py`，`627 passed`，外部連線嘗試 0 次。前後對照（S10a）在資料複本上執行，本機 `data/` 還沒全量重新發佈，策略快取也還沒重算。第 4 階段的判定依據 S10b 的逐條覆蓋表；本次文件更新對照目前工作樹重新核對了行號與測試名稱，除了讀取 AI 提示檔的 4 個測試檔（`52 passed`，見 recheck 文末）之外沒有重跑測試。
- 第 3、6 階段的數字是對當時的工作樹（HEAD `e737206` 加上未提交的修改）執行的結果，程式之後原樣提交為 `ac16ee5`；當時沒有重跑 `tests\backtest` 全目錄、完整後端 test suite 與 `pnpm build`（第 4 階段的 S10b 全量回歸已涵蓋）。收盤集合競價的 MIS 行為只用合成樣本測過，TWSE 休市表的實際端點只用注入的資料測過。

- 第 1 階段已全部提交；標「第 1 階段全部完成後」的數字是對當時工作樹執行的結果。第 2 階段的測試數字是對提交前的工作樹執行的結果，程式之後原樣提交為 `b3a8007`。`pnpm build` 第 1 階段由實作者執行（結束碼 0），第 2 階段的文件同步沒有重跑。
- 第 1、2 階段之後都沒有執行完整的後端 test suite，只跑了上表列出的範圍；第 2 階段也沒有重跑 `tests\backtest` 全目錄。
- 第 2 階段的資料寫入不是文件更新執行的；本頁對本機資料的描述來自唯讀核對，其中上線經過、新上市日K、enriched 換手率與參考價、下市公告等項目引用協調者當天的核對。
- 其餘各組沒有跑測試，判定靠靜態閱讀與唯讀抽查。本輪沒有執行即時市場實跑或歷史資料回補。enriched 的全量重新發佈不是本次文件同步執行的，這裡只做唯讀核對。
- 2026-10-04 記錄的驗收命令（`152 passed`、`147 passed`、`91 passed` 等）沒有涵蓋 `test_worker_process.py`，所以當時寫的「獨立驗收沒有 P0–P3 finding」，只對那幾個測試檔成立。
- 原始重現資產 `findings_index.json`、`coverage.json`、`reference/manifest.json` 與部分腳本沒有納入版本庫，原稽核的數字無法只靠目前倉庫完整重跑。
- `docs/audit-2026-09-24/sections/` 目前也沒有納入版本庫，本頁對各章的連結只在本機有效。
- 原章節中 2026-09-24 的個別樣本數、影響檔數與百分比沒有用目前資料重跑，不應直接當成現況統計。
- A12 被推翻 finding 的章節標題仍殘留 `P3 → P3`，內文才標 `none`。
- 法規口徑沿用 2026-10-04 依[財政部證券交易稅條例](https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079&kw=0.00150.&media=print)、[臺灣證券交易所 ETF 規章](https://www.twse.com.tw/zh/products/securities/etf/overview/rules.html)、[臺灣證券交易所交易制度](https://www.twse.com.tw/zh/products/system/trading.html)與[櫃買中心當沖規則](https://www.tpex.org.tw/zh-tw/mainboard/trading/day-trading/rules.html)所做的重核，本輪沒有重新查證；新上市規則的條文例外，已於 2026-10-06 查證。第 2 階段新增的依據：TDR 證交稅 0.1% 依台財稅第811672688號函（[財政部稅務入口網問答](https://www.etax.nat.gov.tw/etwmain/tax-info/understanding/tax-q-and-a/national/securities-transaction-tax/filing/LeJ1WWV)），TDR 當沖適用 0.1% 是我們的解讀；上櫃 ETF 的成分範圍與主被動型態依 TPEx ETF/detail（2026-10-07 查詢）；這兩項本次文件同步沒有重新連線查證。券商折扣與最低手續費維持為可設定的模擬參數，不宣稱是法定固定值。

## 維護方式

1. 修正一個 finding 時，同步更新 [recheck-2026-10-06.md](recheck-2026-10-06.md) 該列的判定與證據，再更新本頁的「最新章節狀態」、「已確認完成的主要修正」與「目前待修正項目」。下一次全面重核時，另開新的日期檔，不覆蓋舊檔。
2. 只有直接覆蓋原失效情境的測試通過，才標成「已修正」；只有程式碼改了，或 commit message 提到修正，都不夠。
3. 驗收命令要涵蓋受影響模組的整個測試目錄，不能只挑新增的測試檔。
4. 原始章節保留作為 2026-09-24 的證據底稿，不回填新的程式碼行號；新證據寫在重核檔與本頁。
5. 每次重核都要記錄 commit、日期、實際命令與未驗證的範圍，避免把歷史統計誤當成目前狀態。

---

# 附錄：2026-09-24 原始稽核基線

> 2026-10-06 註記：本附錄是 2026-09-24 的基線，內容不回填。其中「前端目前沒有回測頁」、「只有直接呼叫 API 才會碰到回測」等前提，在 `f404a85` 新增回測頁後已不成立；各 finding 的現況以 [recheck-2026-10-06.md](recheck-2026-10-06.md) 為準。

> 範圍：stock_analyze_panel 的所有指標、策略、統計、股價、市值、換手率、公式、策略合理性與訊號。
> 稽核對象：commit `ced12a5`（分支 feat/wire-unreachable-settings）。章節檔引用的程式碼行號以這個 commit 為準，之後的修改會讓行號偏移。
> 輸入：本資料夾的 `sections/*.md`，以及稽核暫存目錄的 `findings_index.json`、`coverage.json`、`reference/manifest.json`。暫存目錄沒有納入版本庫；章節檔中的 `AUDIT_SCRATCH/` 指這個暫存目錄，文中提到的 scratchpad 是它的上層目錄。
> 本附錄由單一 agent 依上述輸入整理，沒有重跑任何驗證、沒有讀專案程式碼、沒有發出網路請求。根因的歸併、「性質」與排序屬整理者判斷。放進 docs/ 前經獨立驗收核對，並依驗收結果修正 RC20 的請求次數估計，把被推翻的 1 條排除在嚴重度分布之外。

## 1. 結論

**整體判斷**：算式大致正確，問題集中在「餵進去的資料」與「台股口徑」。

- 各章比對後確認正確的有 272 項，例如開高低收與證交所 STOCK_DAY 逐筆一致，KD、Wilder RSI、MA／EMA、布林、ATR、融資融券與券資比、非除權息日的漲跌幅都與官方或第三方吻合。多數章節的稽核者摘要也是同樣結論，例如 A06「公式本身大致正確，錯誤出在資料與口徑」、A09「問題不在公式本身，而在輸入資料與口徑」、G03「研究脈絡的公式本身正確，出錯的是餵給它的資料和日期基準」。
- 195 條 finding 經獨立驗證：確認成立 108、部分成立 86、被推翻 1。成立的 194 條中沒有 P0；P1 27 條、P2 64 條、P3 103 條。
- 依根因合併後是 36 個根因，另有 7 條單項（含被推翻的 1 條）。27 條 P1 全部落在前 8 個根因，其中前 3 個（除權息、上櫃缺漏、16:30 初值）就占 21 條。

**最該優先處理的 5 個根因**（先看嚴重度，再看對你的實際影響）：

1. **RC01 台股沒有除權息資料**（P1，16 條，橫跨 14 章）：你的 9 檔中有 7 檔在 2026-06～09 有除息，0050 在 2025-06 一拆四。除權息當天的漲跌幅、分時圖方向與漲跌停線、異常清單、策略訊號都錯；跨缺口的 MA、KD、RSI、關鍵價位與回測也失真。
2. **RC02 盤後抓取把不完整的結果當成完成**（P1，9 條）：9/16、9/23 的全市場統計、選股、策略、異常診斷少了整個上櫃，卻標為 complete。9/24 起上櫃漲跌幅跨兩天計算。不手動 `--force` 重抓就不會修復，下次櫃買抓取失敗會再發生。
3. **RC03 16:30 抓取的量額與法人數字是初值，之後永不重抓**（P1，11 條）：你的自選股成交量、量比、換手率與投信累計都可能偏低（2454 投信 5 日累計少算約 40%）。RC02 與 RC03 共用「分區已存在就跳過」的機制，可以一起修。
4. **RC04 全市場法人買賣超以股數加總**（P1，2 條）：首頁與選股頁的外資、投信買賣超，方向可能與實際資金相反。
5. **RC05 同一指標在選股器、研究脈絡、自選頁各自重算**（P1，7 條）：選股頁的 RSI(14) 與量比和自選頁、K 線不同（RSI 最多差 21 點；「量比 ≥2」9/22 篩出 182 檔，管線口徑為 333 檔）；自選頁的 KDJ、RSI6、RSI24、振幅永遠是「—」。

RC06～RC08 也是 P1，但主要影響回測（RC06 另影響已存的連續漲跌停天數與分時圖的漲跌停參考線）。前端目前沒有回測頁，只有直接呼叫 API 才會碰到回測。建議（整理者判斷）：這三項修好之前，不要採信任何台股回測結果。

## 2. 依根因合併的問題清單

### 2.0 讀法

- **你的情境**：純台股、即時行情開啟、監控規則 0 條，自選 9 檔都是上市：股票 2454、3034、2379、6196、3545；ETF 0050、00878、00885、00893，其中 00885、00893 含國外成分，沒有漲跌幅限制。因此：
  - 告警相關的後果目前不會發生。
  - 盤中與即時路徑的問題（RC09～RC11）每天都在作用。
  - 上櫃相關的問題不直接影響你的自選個股，但會影響全市場統計、選股、策略與異常清單。
- **排序**：先依根因內最高的最終嚴重度（P1 → P2 → P3），同級再依對上述情境的實際影響。同級內的先後是整理者的判斷。
- **歸屬**：每條 finding 只歸入一個主要根因，涉及兩個根因的以「另見」標註，所以各根因條數加總恰為 195。
- **性質**：「單一根因」表示同一段程式或同一個資料缺口造成，一次修正可以涵蓋全部 finding；「同類歸併」表示性質相近但各自獨立，需要逐項修。
- **符號**：finding 後的括號是最終嚴重度與狀態。✓＝確認成立；△＝部分成立，驗證者修正過部分敘述或數字，以章節檔中的「修正後敘述」為準。
- **找原文**：finding id 的格式是「章代號#序號」，序號是該章稽核者原始清單的順序（從 0 起算）。章節檔依狀態與嚴重度重新排序，標題也不含序號，請用附錄的標題在章節檔中搜尋。

### 2.1 根因總表

| # | 根因 | 最高嚴重度 | 條數 | 嚴重度組成 | 確認成立／部分成立 | 章數 | 性質 |
|---|---|---|---|---|---|---|---|
| RC01 | 台股沒有除權息資料 | P1 | 16 | P1×12、P2×3、P3×1 | 7／9 | 14 | 單一根因 |
| RC02 | 盤後抓取把不完整的結果當成完成（上櫃整日缺漏） | P1 | 9 | P1×6、P2×1、P3×2 | 7／2 | 7 | 單一根因（含 2 條同類） |
| RC03 | 16:30 抓取的量額與法人是初值，之後永不重抓 | P1 | 11 | P1×3、P2×7、P3×1 | 6／5 | 11 | 單一根因 |
| RC04 | 全市場法人買賣超以股數跨證券加總 | P1 | 2 | P1×1、P2×1 | 1／1 | 2 | 單一根因 |
| RC05 | 同一指標在選股器、研究脈絡、自選頁各自重算 | P1 | 7 | P1×1、P2×2、P3×4 | 4／3 | 5 | 單一根因 |
| RC06 | 漲跌停價與升降單位有多份實作，部分沿用 A 股取整 | P1 | 7 | P1×2、P2×1、P3×4 | 3／4 | 4 | 單一根因 |
| RC07 | 回測沒有接上台股交易規則 | P1 | 4 | P1×1、P3×3 | 2／2 | 1 | 單一根因 |
| RC08 | 回測使用成交當下還不可知的資訊 | P1 | 2 | P1×1、P3×1 | 2／0 | 1 | 同類歸併 |
| RC09 | 盤中增量路徑另有一套遞推實作 | P2 | 10 | P2×6、P3×4 | 8／2 | 4 | 單一根因 |
| RC10 | 盤中量能口徑混用、時間折算錯誤 | P2 | 5 | P2×3、P3×2 | 2／3 | 4 | 同類歸併 |
| RC11 | 沒有休市日曆與收盤集合競價的時段模型 | P2 | 4 | P2×3、P3×1 | 3／1 | 3 | 單一根因 |
| RC12 | 漲跌幅限制不分商品別與上市狀態 | P2 | 3 | P2×3 | 3／0 | 2 | 單一根因 |
| RC13 | 缺值被當成 0 分、0 值或排在最前 | P2 | 6 | P2×5、P3×1 | 2／4 | 5 | 同類歸併 |
| RC14 | 證券主檔與商品分類不完整 | P2 | 6 | P2×3、P3×3 | 5／1 | 4 | 同類歸併 |
| RC15 | 大盤指數沒有接到畫面 | P2 | 3 | P2×1、P3×2 | 2／1 | 2 | 單一根因 |
| RC16 | 顯示格式、單位與用語 | P2 | 6 | P2×1、P3×5 | 3／3 | 4 | 同類歸併 |
| RC17 | 名稱或說明與實際計算不符 | P2 | 8 | P2×3、P3×5 | 5／3 | 6 | 同類歸併 |
| RC18 | 分鐘資料的缺口與分時圖口徑 | P2 | 4 | P2×2、P3×2 | 3／1 | 2 | 同類歸併 |
| RC19 | 選股「逼近漲停／跌停」以當日收盤為基準 | P2 | 2 | P2×2 | 2／0 | 2 | 單一根因 |
| RC20 | 對官方 openapi 的抓取沒有快取與容錯 | P2 | 3 | P2×1、P3×2 | 2／1 | 2 | 同類歸併 |
| RC21 | 關鍵價位模組的定義與實作細節 | P2 | 6 | P2×2、P3×4 | 5／1 | 1 | 同類歸併 |
| RC22 | A 股遺留的資料表、指數與 API 仍被使用 | P2 | 5 | P2×1、P3×4 | 3／2 | 4 | 同類歸併 |
| RC23 | 個別策略的條件、評分方向或門檻偏離宣稱 | P2 | 8 | P2×2、P3×6 | 2／6 | 3 | 同類歸併 |
| RC24 | 策略矩陣的滾動窗口遇缺列即變成 NaN | P2 | 3 | P2×3 | 0／3 | 2 | 單一根因 |
| RC25 | 暖機門檻缺失、結果隨載入的歷史長度漂移 | P2 | 11 | P2×4、P3×7 | 2／9 | 7 | 同類歸併 |
| RC26 | 異常診斷的門檻、基期與母體細節 | P2 | 4 | P2×1、P3×3 | 2／2 | 1 | 同類歸併 |
| RC27 | TDR 與外幣面額公司被當成一般國內股票 | P2 | 3 | P2×1、P3×2 | 3／0 | 3 | 單一根因 |
| RC28 | composite 疊加策略的選股與回測合併口徑不一 | P2 | 3 | P2×1、P3×2 | 3／0 | 2 | 單一根因（含 1 條同類） |
| RC29 | 換手率的定義與分母沒有對齊台股慣例 | P3 | 4 | P3×4 | 3／1 | 3 | 同類歸併 |
| RC30 | 布林通道用樣本標準差 | P3 | 4 | P3×4 | 1／3 | 4 | 單一根因 |
| RC31 | 以 252 日年化、以自然日代替交易日 | P3 | 4 | P3×4 | 2／2 | 3 | 同類歸併 |
| RC32 | 數值邊界：浮點相等、0/0、先捨入再比門檻 | P3 | 4 | P3×4 | 0／4 | 4 | 同類歸併 |
| RC33 | 法人／融資「缺列」的語意不一致 | P3 | 3 | P3×3 | 0／3 | 2 | 單一根因 |
| RC34 | 缺資料被標成「不適用」、品質標示寫死 | P3 | 4 | P3×4 | 3／1 | 2 | 同類歸併 |
| RC35 | 成交量門檻以固定股數計 | P3 | 2 | P3×2 | 0／2 | 2 | 單一根因 |
| RC36 | 進場訊號一律標成 MA5/MA20 黃金交叉 | P3 | 2 | P3×2 | 2／0 | 2 | 單一根因 |
| 單項 | 其他單項問題（見 2.3） | P2 | 7 | P2×1、P3×5（另 1 條被推翻） | 5／1（另 1 條被推翻） | 4 | — |
| 合計 | | | 195 | P1×27、P2×64、P3×103（另 1 條被推翻） | 108／86（另 1 條被推翻） | | |

### 2.2 各根因說明

#### RC01｜台股沒有除權息資料：價格未還原，除權息日也沒有改用參考價

- **最高嚴重度**：P1（P1×12、P2×3、P3×1）｜**狀態**：確認成立 7、部分成立 9｜**性質**：單一根因｜**涉及章**：A01、A02、A03、A04、A05、A06、A08、A09、A10、A11、A12、G01、G04、G05
- **說明**：台股 provider 宣告 `adj_factor=False`、`enriched_refresh` 傳入空的復權因子，所以 enriched 的開高低收就是原始價（違反 CONTRIBUTING §3.2 的前復權契約）；除權息日的漲跌幅、漲跌停基準與昨收也都用除權息前的原始收盤，而不是官方除權息參考價，使跨除權息與分割的指標、訊號、策略、回測（也不計股利）與關鍵價位全部失真。
- **對你的影響**：
  - 你的 9 檔中有 7 檔在 2026-06～09 有除息：2454 7/7、3034 7/10、2379 9/8、6196 6/24、3545 8/4、0050 7/21、00878 8/18（取自 manifest 的 watchlist_coverage）。0050 另在 2025-06-18 一拆四。
  - 除息當天：K 線資訊列與分時圖的漲跌幅、線色、漲跌停線都以原始昨收計算。例如 6196 6/24 顯示 −0.18%，官方 +0.93%；2379 9/8 顯示 −5.08%，以參考價計為 −1.70%。
  - 異常清單把 2379 9/8 列為大幅下跌；首頁跌停家數多算（9/23 app 3 檔，官方 1 檔）。
  - 跨缺口：0050「1年」K 線前段的 RSI、KD 失真（RSI14 假超賣 36 天）。00878 等配息 ETF 每次配息出現假的 MA20 跌破，之後 20～60 天內進不了多頭排列。0050 的關鍵價位出現高於現價 63%～68% 的前高。
  - 回測把股利算成虧損（目前只能經 API 觸發）。
- **備註**：驗證者用 TWT49U 證實，除權息日漲停取 max(除權息參考價, 減除股利參考價)、跌停取 min(兩者)，可以 876/876 重現官方漲跌停價。
- **finding**：`A01-price-data#1`（P1✓）、`A01-price-data#2`（P2△）、`A02-ma-macd#0`（P1△）、`A03-kd-rsi#0`（P1△）、`A04-boll-atr-vol#0`（P1✓）、`A05-limits-signals#2`（P2△）、`A06-turnover-mcap-pct#0`（P1✓）、`A08-strategies-trend#2`（P1△）、`A09-strategies-momentum#0`（P1△）、`A10-strategies-reversal#0`（P1✓）、`A11-backtest-costs#2`（P1✓）、`A12-screener-stats#1`（P1△）、`G01-gap#0`（P1△）、`G04-gap#2`（P2△）、`G04-gap#4`（P3✓）、`G05-gap#0`（P1✓）

#### RC02｜盤後抓取把不完整的結果當成完成：上櫃整日缺漏、標為 complete、永不補抓

- **最高嚴重度**：P1（P1×6、P2×1、P3×2）｜**狀態**：確認成立 7、部分成立 2｜**性質**：單一根因（含 2 條同類）｜**涉及章**：A01、A06、A08、A12、A13、G01、G03
- **說明**：盤後快照在櫃買抓取失敗時只記 warning、回傳成功的一半，`refresh_dates` 照樣寫入分區並回報 success，之後的排程看到分區已存在就不再補抓，市場與產業統計、異常診斷也只檢查分區是否存在就標為 complete（同類：快照解析不比對回應日期 `A01-price-data#7`、股本刷新時一個交易所失敗就整檔覆寫 `A06-turnover-mcap-pct#5`）。
- **對你的影響**：
  - 自選 9 檔都是上市，個股欄位不直接受影響。
  - 9/16、9/23 的全市場功能少了整個上櫃（約 1,000 檔），而且沒有任何警示：9/23 首頁成交額少約 2,532 億（約 22%）、漲跌家數少約 978 家；策略推薦 0 檔上櫃（9/22 上櫃約占三成）；異常診斷 9/17～9/22 的上櫃爆量訊號全部消失；AI 報告的大盤脈絡只有上市。
  - 9/24 起上櫃漲跌幅會跨兩天計算，上櫃個股 K 線缺兩根，要手動 `--force` 才會補回。之後只要櫃買抓取再失敗就會重演。
- **備註**：與 RC03 共用「分區已存在就跳過」（`run_update` 只補 `d_max` 之後的日期、`refresh_dates` 跳過既有分區）。驗證者建議在 21:40 那班或隔日強制重抓最近 1～2 個交易日並比對總量：隔日抓取已證實與官方一致，而 9/18 在 17:03 抓取仍偏低，只延後排程時間沒有證據足以解決。另見 RC03 的 `A09-strategies-momentum#5`，它同時記錄了 9/23 分區缺上櫃。
- **finding**：`A01-price-data#0`（P1✓）、`A01-price-data#7`（P3△）、`A06-turnover-mcap-pct#2`（P1✓）、`A06-turnover-mcap-pct#5`（P3△）、`A08-strategies-trend#0`（P1✓）、`A12-screener-stats#0`（P1✓）、`A13-realtime#0`（P1✓）、`G01-gap#1`（P2✓）、`G03-gap#0`（P1✓）

#### RC03｜16:30 抓取的量額與法人數字是初值，之後永不重抓

- **最高嚴重度**：P1（P1×3、P2×7、P3×1）｜**狀態**：確認成立 6、部分成立 5｜**性質**：單一根因｜**涉及章**：A01、A04、A06、A07、A09、A10、A12、A13、G01、G03、G05
- **說明**：日K（MI_INDEX）與三大法人（T86、TPEx）在當天 16:19～17:03 之間落盤時，官方還沒併入開放到 17:00 的鉅額交易、投信數字之後也會更正，而排程看到分區已存在就跳過，初值因此永久留在 daily store 與 enriched（隔日才抓的分區與官方完全一致）。
- **對你的影響**：
  - 自選股直接受影響：2454 連 5 天成交量偏低 0.08%～4.19%，6196 在 9/17 少 16.95%，3034、2379、3545 也有偏低的日子。量比、均量、換手率與 K 線的 VOL5、量比標籤跟著偏（「量比1」最多偏 20%）。
  - 2454 投信 5 日累計少算約 40%，9/17 的單日方向相反。
  - 全市場：9/23 有 23 檔量偏低，合計少約 39 億元成交值；市場投信合計比官方定版高 59%；2330 在 9/18 的量比算成 1.93（官方口徑 2.11），因而漏報爆量。
- **備註**：修法與 RC02 相同（見 RC02 備註）。`A09-strategies-momentum#5` 也記錄了 9/23 分區缺上櫃（另見 RC02）。
- **finding**：`A01-price-data#3`（P2△）、`A04-boll-atr-vol#10`（P2△）、`A06-turnover-mcap-pct#1`（P1△）、`A07-chips#0`（P1✓）、`A09-strategies-momentum#5`（P1✓）、`A10-strategies-reversal#14`（P3△）、`A12-screener-stats#4`（P2△）、`A13-realtime#4`（P2✓）、`G01-gap#4`（P2✓）、`G03-gap#3`（P2✓）、`G05-gap#4`（P2✓）

#### RC04｜全市場法人買賣超以股數跨證券加總

- **最高嚴重度**：P1（P1×1、P2×1）｜**狀態**：確認成立 1、部分成立 1｜**性質**：單一根因（兩章各報一次）｜**涉及章**：A07、A12
- **說明**：`market_intelligence` 把當日所有證券的外資、投信、自營淨股數直接加總（混入 ETF 單位數；上櫃另含約 4,600～5,200 列權證，上市不含），前端再除以 1000，以「張」加紅綠色顯示，而市場層級的法人買賣超慣例以金額計。
- **對你的影響**：首頁「外資買賣超」與選股頁「外資／投信買賣超」可能與實際資金方向相反。例如 9/23 投信畫面顯示 +20,232.552 張（紅色），上市投信實際賣超 47.59 億（HiStock）。研究脈絡的 `institutional_market_net` 也是同一口徑。
- **finding**：`A07-chips#2`（P2✓）、`A12-screener-stats#3`（P1△）

#### RC05｜同一指標在選股器、研究脈絡、自選頁各自重算，口徑與 enriched 不一

- **最高嚴重度**：P1（P1×1、P2×2、P3×4）｜**狀態**：確認成立 4、部分成立 3｜**性質**：單一根因｜**涉及章**：A02、A03、A04、A06、A12
- **說明**：選股器（`taiwan/screener.py`）、研究脈絡（`research_context.py`）與自選頁（`technical_indicators.py`、`watchlist_enrichment.py`）沒有讀 enriched 或共用 pipeline 函式，各自重算出同名但不同義的值：RSI(14) 用 Cutler 簡單平均、`vol_ratio_5d` 分母含當日、不過濾停牌與無成交列，自選頁只算 `rsi_14` 且只用盤後資料。
- **對你的影響**：
  - 同一檔在選股頁與自選頁的 RSI 最多差 21.27 點（00885 9/8：74.52 對 53.25）；00878 9/23 在選股頁是 77.45（標為超買），自選頁是 69.26。
  - 選股「放量股」（量比 ≥2）9/22 篩出 182 檔，管線口徑為 333 檔；AI 研究引用的量比與 K 線對不上。
  - 自選頁的 KDJ、RSI6、RSI24、振幅欄永遠是「—」，RSI14 盤中是前一交易日的值；3545 停牌後幾天，自選頁的動能與均量和 K 線不同。
- **finding**：`A02-ma-macd#7`（P3✓）、`A03-kd-rsi#1`（P2✓）、`A03-kd-rsi#5`（P3✓）、`A04-boll-atr-vol#4`（P1△）、`A04-boll-atr-vol#9`（P3△）、`A06-turnover-mcap-pct#11`（P3✓）、`A12-screener-stats#5`（P2△）

#### RC06｜漲跌停價與升降單位有多份實作，部分沿用 A 股 0.01 元四捨五入

- **最高嚴重度**：P1（P1×2、P2×1、P3×4）｜**狀態**：確認成立 3、部分成立 4｜**性質**：單一根因｜**涉及章**：A05、A11、G03、G05
- **說明**：台股漲跌停與升降單位規則有多份實作：回測的 `numpy_limit_price`／`polars_limit_price`、盤後連板數與前端分時線都用 A 股「四捨五入到 0.01 元」，另有 10000 哨兵值、三份沒有日期維度的檔位表、研究脈絡的 `tick_size` 不帶 ETF 類別，沒有共用已經正確的 PriceLimitModel／TickSizeModel。
- **對你的影響**：
  - 回測（目前只能經 API）把大多數一字漲跌停當成可成交：本地 2024-01～2026-09 的一字漲停有 1,977／2,303（86%）被當成可買，9/23 的 15 檔漲停只偵測到 2 檔。
  - 已存的連續漲停天數低估（9/23 的 15 檔漲停中 13 檔記為 0）。你沒有監控規則，只影響之後的自訂訊號與回測因子。
  - 分時圖「漲跌停」模式的參考線最多偏 1 個升降單位（2454 畫 5698／4662，官方 5695／4665）；漲停價破萬的 2059、5274 無法標出漲停。
  - 千元以上升降單位預計 2027 年 7 月改為 1 元（仍待主管機關核准），目前的檔位表屆時會出錯。
- **finding**：`A05-limits-signals#0`（P1△）、`A05-limits-signals#1`（P2△）、`A05-limits-signals#13`（P3△）、`A05-limits-signals#14`（P3△）、`A11-backtest-costs#1`（P1✓）、`G03-gap#4`（P3✓）、`G05-gap#6`（P3✓）

#### RC07｜回測沒有接上台股交易規則

- **最高嚴重度**：P1（P1×1、P3×3）｜**狀態**：確認成立 2、部分成立 2｜**性質**：單一根因｜**涉及章**：A11
- **說明**：正式回測路徑的 MatcherConfig 從未傳入 `market_profile`，預設成本是 A 股口徑（佣金 0.02% 雙邊、證交稅 0，挖掘另用 A 股印花稅 0.05%），每單位寫死 100 股；TaiwanMarketProfile 只存在沒有正式呼叫端的舊 `simulate()` 路徑，而台股回測測試只測那條舊路徑，profile 的賣出稅率也不分 ETF。
- **對你的影響**：前端沒有回測頁、挖掘排程關閉、`data/backtest_results` 是空的，目前只有直接呼叫 API 才會碰到。但任何台股回測的報酬、勝率、Sharpe 都被高估（一般股每筆來回約 0.45 個百分點），交易明細的張數欄高估 10 倍。2454 這類高價股在整股規則下買不起、零股可買 40 股，app 卻給 0 股；中價股則買進台股不存在的 300 股「整股」。
- **finding**：`A11-backtest-costs#0`（P1✓）、`A11-backtest-costs#4`（P3△）、`A11-backtest-costs#5`（P3✓）、`A11-backtest-costs#10`（P3△）

#### RC08｜回測使用成交當下還不可知的資訊

- **最高嚴重度**：P1（P1×1、P3×1）｜**狀態**：確認成立 2｜**性質**：同類歸併｜**涉及章**：A11
- **說明**：`minute_fill` 的參考線 MA5 含成交當日收盤（未來函數），`close_t` 進場時移動停損與停利的峰值又含進場前的當日高點。
- **對你的影響**：`minute_fill` 預設關閉、分鐘資料只有 4 個交易日，策略回測 API 預設隔日開盤進場，所以目前實際影響極小；分鐘資料累積後，精確成交的績效會帶未來資訊。
- **finding**：`A11-backtest-costs#3`（P1✓）、`A11-backtest-costs#8`（P3✓）

#### RC09｜盤中增量路徑另有一套遞推實作，與盤後全量口徑不一

- **最高嚴重度**：P2（P2×6、P3×4）｜**狀態**：確認成立 8、部分成立 2｜**性質**：單一根因｜**涉及章**：A02、A03、A04、A05
- **說明**：盤中增量路徑（`_build_live_agg`／`_flush_live_enriched`）用 90 個日曆天代替 60 個交易日、MACD 的 EMA 與 DEA 暖機長度不同、RSI 遞推只用 90 天、60 日高低改用最高最低價、布林改用母體標準差與 >=、交叉訊號從第二輪起拿上一輪快照當前值，預熱時的回退還讀台股不寫入的 `kline_daily`。
- **對你的影響**：你開著即時行情，K 線最後一根活蠟燭與盤中選股、策略看到的值會和收盤後不同。
  - 2454 的 DIF 可差約 5 點、柱差約 8 點；約 4% 的「個股×交易日」金叉或死叉判斷相反。
  - 「創60日新高」盤中幾乎不觸發（2454 9/21～9/23 盤中為否、盤後為是）；當日布林點比前一根窄 2.6%，出現折角。
  - 每年約 2～5 月 MA60 偏低 4%～11%、`momentum_60d` 最多差 29 個百分點（9 月不在此期間，但每年重演）。
  - 啟動預熱的數十秒內，活蠟燭的 MA、MACD 錯誤或空白。
  - 監控規則 0 條，所以沒有誤發警示。
- **finding**：`A02-ma-macd#1`（P2△）、`A02-ma-macd#2`（P2✓）、`A02-ma-macd#3`（P2✓）、`A02-ma-macd#5`（P3✓）、`A03-kd-rsi#3`（P3✓）、`A04-boll-atr-vol#1`（P2✓）、`A04-boll-atr-vol#2`（P2✓）、`A04-boll-atr-vol#3`（P2△）、`A04-boll-atr-vol#11`（P3✓）、`A05-limits-signals#9`（P3✓）

#### RC10｜盤中量能口徑混用、時間折算錯誤

- **最高嚴重度**：P2（P2×3、P3×2）｜**狀態**：確認成立 2、部分成立 3｜**性質**：同類歸併｜**涉及章**：A04、A06、A13、G05
- **說明**：盤中量能把 MIS 的整股一般交易量（v）與官方全日量（含零股、盤後定價、鉅額）放進同一個比值，自選頁換手率只要報價是當天就用 MIS 量，時間折算又以秒計（開盤數秒放大上千倍），前端 K 線的「量比N」則完全不折算。
- **對你的影響**：
  - 自選頁預設顯示的換手率，從收盤到午夜都是整股口徑（2454 為 0.3677%，官方口徑 0.4007%，低 8.2%），午夜後變成「—」；16:30～24:00 自選頁與預覽視窗的換手率不同。
  - 盤中量比偏低約 4%～13%；開盤第一分鐘量比被放大上千倍（09:00:05 約 3240 倍），盤中選股與策略出現大量假放量。
  - K 線副圖預設開啟的「量比N」在盤中持續低估（10:00 只剩標準值約 22%）。
- **finding**：`A04-boll-atr-vol#5`（P3△）、`A04-boll-atr-vol#6`（P3✓）、`A06-turnover-mcap-pct#3`（P2△）、`A13-realtime#3`（P2△）、`G05-gap#1`（P2✓）

#### RC11｜沒有台股休市日曆與收盤集合競價的時段模型

- **最高嚴重度**：P2（P2×3、P3×1）｜**狀態**：確認成立 3、部分成立 1｜**性質**：單一根因｜**涉及章**：A06、A13、G03
- **說明**：交易日曆沒有國定假日、排程週一到週五都跑，研究脈絡 16:00 就把目標日切到當天（日線 16:30 才寫入），13:30 收盤集合競價的成交被即時閘門與分鐘錄製丟棄、同步卻記為完成，13:25～13:30 也被當成連續交易。
- **對你的影響**：
  - 每個交易日 13:30～16:30，預覽卡片、K 線、指標與量比停在集合競價前的價量，與自選清單的收盤價不同（18 個樣本中 11 個不符，最大差 0.54%），分鐘圖缺最後一筆成交。
  - 每天 16:00～16:30，研究卡、比較頁與 AI 報告拿不到價格，previous_close 與漲跌停價退回前兩日。
  - 即將到來的 9/25 中秋、9/28 教師節：9/25 的重新發佈會清空最近交易日的換手率；9/25 16:00 起到 9/29，研究卡、比較頁、AI 都拿不到價格。
  - 13:25～13:30 的告警問題因為沒有監控規則，目前不影響你。
- **finding**：`A06-turnover-mcap-pct#4`（P2✓）、`A13-realtime#1`（P2✓）、`A13-realtime#7`（P3✓）、`G03-gap#2`（P2△）

#### RC12｜漲跌幅限制不分商品別與上市狀態

- **最高嚴重度**：P2（P2×3）｜**狀態**：確認成立 3｜**性質**：單一根因｜**涉及章**：A05、G05
- **說明**：漲跌幅比例對所有台股一律給 10%，沒有區分含國外成分 ETF（無漲跌幅限制）與新上市前 5 個交易日（無漲跌幅），報價面板還把小數比例直接當百分比顯示。
- **對你的影響**：自選的 00885、00893 在分時圖「漲跌停」模式會畫出不存在的 ±10% 線，單日波動超過 10% 時，自適應模式的範圍會被截斷；報價面板的漲跌停資訊錯誤（比例顯示成 0.1%）。新股蜜月期出現假漲停、假跌停訊號（已產生 29 筆）。
- **finding**：`A05-limits-signals#3`（P2✓）、`A05-limits-signals#8`（P2✓）、`G05-gap#5`（P2✓）

#### RC13｜缺值被當成 0 分、0 值或排在最前

- **最高嚴重度**：P2（P2×5、P3×1）｜**狀態**：確認成立 2、部分成立 4｜**性質**：同類歸併｜**涉及章**：A08、A09、A10、A12、G04
- **說明**：缺值沒有被當成缺值：`build_matrix_score` 只要任一評分欄是 NaN 就給 0 分並保留候選，polars 排序預設把 null 排在最前，關鍵價位把 null 換手率當成 0；觸發來源多半是換手率只有最新一日有值、ETF 沒有分母（見 RC29）。
- **對你的影響**：
  - 你的 0050、00878、00893 在 bullish_alignment、low_volatility_leader、pullback_to_support 清單中都是 0 分、排在所有股票之後（例如 bullish_alignment 的 0050 第 126、00878 第 143），看起來像評分最差，其實只是缺換手率。
  - high_turnover_surge 在 418 個歷史交易日都是 0 筆進場，沒有任何提示；選股依漲跌幅降序時，第一頁 50 列有 46 列是空值。
  - 價格提醒的「成交密集區」其實是約 338 個交易日、沒有衰減的累積成交量分布（9 檔中 7 檔的 POC 低於現價 29%～73%；對話框目前只有 3545 會列出這類價位）。
- **finding**：`A08-strategies-trend#4`（P2△）、`A09-strategies-momentum#3`（P2△）、`A10-strategies-reversal#1`（P2✓）、`A10-strategies-reversal#12`（P3✓）、`A12-screener-stats#7`（P2△）、`G04-gap#0`（P2△）

#### RC14｜證券主檔與商品分類不完整

- **最高嚴重度**：P2（P2×3、P3×3）｜**狀態**：確認成立 5、部分成立 1｜**性質**：同類歸併｜**涉及章**：A01、A05、A10、G06
- **說明**：證券主檔是 9/17 一次建立後不再更新的快照；非一般類型（例如主動式）的 ETF 分類被拒後拋出未攔截的 ValueError；櫃買 CFI「CEOGEU」被當成國內成分；11 檔債券或期貨信託 ETF 被標成 foreign_equity；listing_date 混用三種格式；ETF 存在 stock 分區，使限股票的策略也選進 ETF。
- **對你的影響**：在詳細頁或比較頁搜尋「台灣」「主動」會回 500；點開主動式 ETF（例如 00981A）的詳細頁也會 500，報價面板含這類 ETF 時整個面板失敗。9/17 之後掛牌的 7812、7856 沒有日K，也搜尋不到。4 檔上櫃國外成分 ETF 被給 10% 漲跌幅。你的 4 檔 ETF 分類正確，不受影響。
- **finding**：`A01-price-data#8`（P2△）、`A05-limits-signals#4`（P2✓）、`A05-limits-signals#5`（P2✓）、`A10-strategies-reversal#10`（P3✓）、`G06-gap#1`（P3✓）、`G06-gap#2`（P3✓）

#### RC15｜大盤指數沒有接到畫面

- **最高嚴重度**：P2（P2×1、P3×2）｜**狀態**：確認成立 2、部分成立 1｜**性質**：單一根因｜**涉及章**：A12、G03
- **說明**：個股詳細頁以空陣列呼叫 `parse_taiex_rows`／`parse_tpex_rows`，市場快照的 indexes 寫死 unavailable（本地 index_daily 有資料到 9/17），而 `parse_taiex_rows` 還把第一列的昨收設成開盤價。
- **對你的影響**：每次打開個股詳細頁，「所屬大盤基準」卡都是「--」，這個功能等於不可用。市場快照的指數欄目前沒有畫面使用；若修大盤卡時直接餵當月資料，月初第一天的漲跌會錯。
- **finding**：`A12-screener-stats#9`（P3△）、`G03-gap#1`（P2✓）、`G03-gap#8`（P3✓）

#### RC16｜顯示格式、單位與用語

- **最高嚴重度**：P2（P2×1、P3×5）｜**狀態**：確認成立 3、部分成立 3｜**性質**：同類歸併｜**涉及章**：A02、A06、G04、G05
- **說明**：顯示格式、單位與用語有誤或沿用中國大陸慣例：分時圖把 1e9 當成 1 億、成交量以股數配萬／億且不標單位、1e12 顯示為「萬億」、MACD 柱沿用 2×(DIF−DEA) 並把訊號線標成 DEA、關鍵價位標籤是簡體字。
- **對你的影響**：
  - 分時圖懸停時，10 億以上的分鐘成交額少顯示 10 倍（2454 9/18 12:50 的 58.94 億顯示為 5.89 億，4 天內出現 4 次）。
  - K 線副圖（預設開啟）與資訊列的成交量是股數（例如「643萬」是 643 萬股），容易誤讀成張；「萬億」不是台灣用語。
  - MACD 數值與 Yahoo 奇摩、券商 App 對不上（2454 9/23 app 的「MACD」是 113.229，台灣慣例的 OSC 是 56.614）。
  - 價格提醒對話框與新建規則的名稱出現簡體字。
- **finding**：`A02-ma-macd#4`（P3△）、`A06-turnover-mcap-pct#9`（P3✓）、`A06-turnover-mcap-pct#10`（P3△）、`G04-gap#10`（P3△）、`G05-gap#3`（P2✓）、`G05-gap#7`（P3✓）

#### RC17｜名稱或說明與實際計算不符

- **最高嚴重度**：P2（P2×3、P3×5）｜**狀態**：確認成立 5、部分成立 3｜**性質**：同類歸併｜**涉及章**：A05、A07、A08、G01、G02、G03
- **說明**：訊號與欄位的名稱或說明和實際計算不符：融券「減少」也標成「融券激增」、「價量背離」比的是股價與外資、「跌停翻紅」不要求收盤高於昨收、「60日新高」與「20 日高低」以收盤價計卻沒標示、「近 5 日」卡含 1 日值、財報累計值沒標「累計」、異常嚴重度分級沒有文件。
- **對你的影響**：
  - 選股頁異常表中約 43% 的 SHORT_SURGE 其實是融券大減，卻顯示成「融券激增」，意思相反。
  - 「價量背離」會被理解成量能背離；「跌停翻紅」多數收黑；「要求收盤價在MA60上方」切換不會有任何效果；「近 5 日指標」卡裡的融資變動是單日值。
  - 財報卡的上半年累計營收與淨利，容易被當成 Q2 單季（高估約一倍）。
  - 異常清單的 tooltip 會出現「基期: -375708」這類難懂的值，AI 也可能誤讀。
- **finding**：`A05-limits-signals#7`（P2✓）、`A07-chips#6`（P3✓）、`A08-strategies-trend#7`（P3△）、`G01-gap#2`（P2△）、`G01-gap#6`（P2△）、`G01-gap#7`（P3✓）、`G02-gap#2`（P3✓）、`G03-gap#5`（P3✓）

#### RC18｜分鐘資料的缺口與分時圖口徑

- **最高嚴重度**：P2（P2×2、P3×2）｜**狀態**：確認成立 3、部分成立 1｜**性質**：同類歸併｜**涉及章**：A13、G05
- **說明**：分鐘資料的缺口與量歸屬沒有被正確處理或標示：兩次快照間的累計量差整筆算給觀察到它的那一分鐘、重啟後首筆不計量、分時均價只涵蓋有錄製的時段、`connectNulls` 把缺口連成直線、迷你分時用收盤價簡單平均與等距橫軸。
- **對你的影響**：
  - 個股與多日分時的「均價」與全日均價最多差 100bp（9/23 的分鐘量只占官方日量 21%～55%），畫面沒有標示。
  - 空窗後量柱出現尖峰（9/18 斷網 45 分鐘後，0050 單根 1,516.7 萬股），13:25～13:29 有假量；看不出哪段是沒錄到。
  - 自選表迷你分時的均線與點開後的分時最多差 0.9%。
- **finding**：`A13-realtime#6`（P2△）、`G05-gap#2`（P2✓）、`G05-gap#8`（P3✓）、`G05-gap#9`（P3✓）

#### RC19｜選股「逼近漲停／跌停」以當日收盤為基準

- **最高嚴重度**：P2（P2×2）｜**狀態**：確認成立 2｜**性質**：單一根因（兩章各報一次）｜**涉及章**：A05、A12
- **說明**：選股器的 `_enrich_price_limits` 用當日收盤當漲跌停基準，算出的其實是隔日的漲跌停價，距離恆約 10%。
- **對你的影響**：選股頁「逼近漲停／跌停（≤3%）」永遠 0 筆，而且沒有錯誤提示，看起來像市場沒有這類股票。
- **finding**：`A05-limits-signals#6`（P2✓）、`A12-screener-stats#2`（P2✓）

#### RC20｜對官方 openapi 的抓取沒有快取與容錯

- **最高嚴重度**：P2（P2×1、P3×2）｜**狀態**：確認成立 2、部分成立 1｜**性質**：同類歸併｜**涉及章**：A13、G02
- **說明**：MIS 報價不是即時的時候就每 6 秒下載一次沒有快取的 STOCK_DAY_ALL 再全部丟棄（休市日判斷也缺，見 RC11），個股頁每次請求下載 10 份全市場基本面 JSON，財報 6 類中任一請求失敗整張卡就失敗。
- **對你的影響**：對 openapi.twse.com.tw 產生大量無效請求（你本來就擔心 IP 被限流）：9/18 盤前 25 分鐘就有 221 次，9/25 中秋估計約 2,300～2,700 次；log 中約 118 次開頁造成超過 1,180 次全市場下載，詳細頁的參考資料載入也較慢。上櫃股詳細頁約四分之一機率看不到財報卡（你的自選都是上市，影響小）。
- **finding**：`A13-realtime#2`（P2✓）、`G02-gap#4`（P3✓）、`G02-gap#5`（P3△）

#### RC21｜關鍵價位模組的定義與實作細節

- **最高嚴重度**：P2（P2×2、P3×4）｜**狀態**：確認成立 5、部分成立 1｜**性質**：同類歸併｜**涉及章**：G04
- **說明**：關鍵價位模組（`levels.py`）的定義與實作偏離通行定義或自身說明：樞軸點取最後一根（盤中是未完成 K 棒）、缺口回補判定與 docstring 不符、Keltner 用 SMA 與自訂參數、價位不依升降單位取整、聚合保留價格最高者而非最新者、部分回傳欄位沒有前端使用。
- **對你的影響**：你開著即時行情，盤中開價格提醒對話框時，預設目標價與 R／S 是用未完成 K 棒算的樞軸點，會隨時間漂移（9 檔中 7 檔的預設目標價正好是 P 或 R1）。缺口清單漏掉仍有效的缺口、保留已失效的缺口（00893 的 40.54 被標為支撐）；Keltner 與 TradingView 對不上；約四成價位不在台股報價格點上。你目前 0 條監控規則，代表還沒有用這些價位建立提醒（推測）。
- **finding**：`G04-gap#1`（P2✓）、`G04-gap#3`（P2✓）、`G04-gap#5`（P3✓）、`G04-gap#7`（P3△）、`G04-gap#8`（P3✓）、`G04-gap#9`（P3✓）

#### RC22｜A 股遺留的資料表、指數與 API 仍被使用

- **最高嚴重度**：P2（P2×1、P3×4）｜**狀態**：確認成立 3、部分成立 2｜**性質**：同類歸併｜**涉及章**：A01、A11、A12、G02
- **說明**：A 股遺留的資料表、指數與 API 在台股部署仍在運作或被讀取：`data_integrity` 只掃台股不寫入的 A 股表並建立必定失敗的修復任務、`kline_daily` 殘留日期錯置的舊列、回測基準與 regime 寫死上證指數（regime 從未產出）、10 個財務欄位讀 A 股的 `/api/financials/metrics`（台股資料夾是空的）。
- **對你的影響**：
  - `data_integrity` 看不到台股真正的缺漏（RC02、RC03 都沒被偵測到；9/23 22:03 的啟動檢查還記錄資料完整）。
  - 若你在 9/24～9/29 關閉即時行情後再開啟，會一直收到 409，每次都建立一個永遠修不好的修復任務，並短暫暫停即時行情。
  - 自選清單與資訊列的 10 個財務欄位（EPS、PE(TTM) 等，預設隱藏）永遠空白；回測沒有基準曲線；regime 功能不可用。
- **finding**：`A01-price-data#4`（P2✓）、`A01-price-data#5`（P3△）、`A11-backtest-costs#9`（P3✓）、`A12-screener-stats#8`（P3△）、`G02-gap#1`（P3✓）

#### RC23｜個別策略的條件、評分方向或門檻偏離策略宣稱

- **最高嚴重度**：P2（P2×2、P3×6）｜**狀態**：確認成立 2、部分成立 6｜**性質**：同類歸併（需逐支修）｜**涉及章**：A08、A09、A10
- **說明**：多支內建策略的條件、評分方向或門檻偏離自己的宣稱：bottom_reversal 的出場條件在進場當根就成立、反轉策略評分偏好高 RSI、「爆量」只和前一天比、新低反轉要求收盤就是 60 日最低、縮量回踩允許跌破 MA20 當天進場、整理突破評分偏好遠離低點、突破類評分讓鎖漲停股排前段、factor_rank_research 用截面 min-max 配固定 70/40 門檻。
- **對你的影響**：
  - bottom_reversal 回測約 93%～97% 的交易只持有 1 天（宣稱最長 12 天）；縮量回踩清單約 14% 其實是跌破月線。
  - 突破類清單前段多為鎖漲停、隔日難以依收盤買進（9/23 trend_breakout 前 5 名中有 4 檔鎖漲停）；反轉策略把較不超賣的標的排在前面。
  - 因子挖掘的候選幾乎沒有交易，或次日即出場。
  - 多支反轉策略 9/23 沒有命中，所以選股畫面暫時看不出來。
- **finding**：`A08-strategies-trend#8`（P3△）、`A08-strategies-trend#9`（P3△）、`A09-strategies-momentum#2`（P2△）、`A10-strategies-reversal#2`（P2△）、`A10-strategies-reversal#4`（P3△）、`A10-strategies-reversal#5`（P3✓）、`A10-strategies-reversal#6`（P3△）、`A10-strategies-reversal#9`（P3✓）

#### RC24｜策略矩陣的滾動窗口遇缺列即變成 NaN

- **最高嚴重度**：P2（P2×3）｜**狀態**：部分成立 3｜**性質**：單一根因｜**涉及章**：A08、A09
- **說明**：策略矩陣的時間軸是全市場交易日的聯集，`rolling_quantile`／`rolling_min`／`rolling_max`／`rolling_sum` 用原始列窗口，窗口內有一格缺列就回傳 NaN（`valid_shift` 卻會跳過缺列），lorentzian 的正規化因此產生 0.5 佔位特徵，並帶著真實標籤進入候選池。
- **對你的影響**：
  - 停牌或缺日之後的數十根 K 棒內，布林收斂、整理突破與波段訊號不會觸發，之後再用過期區間產生假訊號（6176 在 9/18 用 5 週前的區間高點 89.5 觸發，實際應為 108）。
  - 0050 在 2025-06-11～10-08 沒有任何布林收斂訊號；9/16 上櫃缺日讓 9/17～9/22 少了 30 檔整理突破。
  - 你的 9 檔中，約 32.7% 的 KNN 預測列，其 8 個近鄰含全 0.5 的佔位列。
- **finding**：`A08-strategies-trend#1`（P2△）、`A09-strategies-momentum#4`（P2△）、`A09-strategies-momentum#7`（P2△）

#### RC25｜暖機門檻缺失、結果隨載入的歷史長度漂移、新股排除沒有實作

- **最高嚴重度**：P2（P2×4、P3×7）｜**狀態**：確認成立 2、部分成立 9｜**性質**：同類歸併｜**涉及章**：A02、A03、A04、A05、A08、A09、A10
- **說明**：EMA／MACD／RSI／ATR 從首筆起算且沒有 `min_samples`，K 線自訂起日、macd_golden 單獨執行、評分虛擬欄位、`ema_bias` 與 lorentzian 候選池的結果都取決於載入了多少歷史，而 `exclude_new_days=30` 在 A08、A10 兩章都被發現從未實作。
- **對你的影響**：
  - 你的 9 檔歷史都很長，今天不受影響。
  - 新上市 ETF 或個股會在首日顯示 RSI 0，並在暖機期觸發 MACD 金叉而被策略選入（2,346 檔中 2,167 檔在第 2 根就觸發交叉）；宣告的「排除上市 30 天內新股」也不生效。
  - lorentzian_knn 的選股與回測在同一天的進場只有 47.5% 一致。
  - K 線自訂起日選在約 7～10 個月前時，前段 RSI 誤差最多約 61 點。
- **finding**：`A02-ma-macd#6`（P3△）、`A03-kd-rsi#2`（P2△）、`A03-kd-rsi#4`（P3✓）、`A04-boll-atr-vol#7`（P3△）、`A05-limits-signals#11`（P3△）、`A08-strategies-trend#3`（P2△）、`A08-strategies-trend#5`（P3△）、`A09-strategies-momentum#1`（P2△）、`A09-strategies-momentum#11`（P3✓）、`A10-strategies-reversal#11`（P3△）、`A10-strategies-reversal#13`（P2△）

#### RC26｜異常診斷的門檻、基期與母體細節

- **最高嚴重度**：P2（P2×1、P3×3）｜**狀態**：確認成立 2、部分成立 2｜**性質**：同類歸併｜**涉及章**：G01
- **說明**：異常診斷有文件沒寫或口徑不一的細節：融資融券多了「20 日中位數 > 0」條件、產業平均含個股自身、券資比訊號沒有最低融資餘額門檻、市場上漲比含 ETF 而產業只含股票。
- **對你的影響**：冷門股與 ETF 最極端的融資融券暴增不會出現在異常清單（33 個交易日有 55 筆未評估，例如 3717 融券一天增加 2,448 張）；小產業的強弱勢股可能漏報；清單混入極小基數的券資比雜訊；個股診斷並列的市場與產業上漲比口徑不同（市場被 ETF 拉高約 5 個百分點）。
- **finding**：`G01-gap#3`（P2△）、`G01-gap#8`（P3✓）、`G01-gap#9`（P3△）、`G01-gap#11`（P3✓）

#### RC27｜TDR 與外幣面額公司被當成一般國內股票

- **最高嚴重度**：P2（P2×1、P3×2）｜**狀態**：確認成立 3｜**性質**：單一根因｜**涉及章**：A06、G02、G06
- **說明**：代碼 91 的臺灣存託憑證（TDR）與外幣面額公司被當成一般國內股票：主檔標成 stock／domestic，換手率分母與市值用「TDR 原股發行股數」，股本卡只抓面額數字、不看幣別。
- **對你的影響**：你的自選不受影響。選股頁的產業情報多出一個「未知產業（91）」偽產業，分類覆蓋率仍報 100%；10 檔不同行業的外國公司被當成同業而觸發相對強弱訊號（例如 910861 神州-DR）；TDR 的換手率與市值失真；TDR 與 KY 公司的股本卡面額幣別錯誤，或顯示「0 元」。
- **finding**：`A06-turnover-mcap-pct#7`（P3✓）、`G02-gap#3`（P3✓）、`G06-gap#0`（P2✓）

#### RC28｜composite 疊加策略的選股與回測合併口徑不一

- **最高嚴重度**：P2（P2×1、P3×2）｜**狀態**：確認成立 3｜**性質**：單一根因（含 1 條同類）｜**涉及章**：A09、A10
- **說明**：composite 的選股合併（`merge_results`）與回測合併（`merge_signal_matrices`）對單一候選與同分的處理不同（選股給 1.0、回測給 0.5，同分名次隨 PYTHONHASHSEED 改變，A09、A10 各報一次），union 模式下子策略權重也幾乎不起作用。
- **對你的影響**：你目前沒有任何 composite 策略，暫不受影響；一旦建立，選股排序與回測持倉會不一致，而嚴格的反轉策略常每天只有 0～2 個候選，會觸發單一候選的路徑。
- **finding**：`A09-strategies-momentum#6`（P3✓）、`A10-strategies-reversal#3`（P2✓）、`A10-strategies-reversal#15`（P3✓）

#### RC29｜換手率的定義與分母沒有對齊台股慣例

- **最高嚴重度**：P3（P3×4）｜**狀態**：確認成立 3、部分成立 1｜**性質**：同類歸併｜**涉及章**：A06、A09、G05
- **說明**：換手率分母含 3 年內不得轉讓的私募股、ETF 沒有分母所以恆為空（6h 決策文件的設計取捨）、high_turnover_surge 的 5% 門檻沿用 A 股流通股慣例、資訊列換手率公式多乘 100（目前因流通股數為空而未觸發）。
- **對你的影響**：自選頁預設顯示的換手率欄，9 列中有 4 列（0050、00878、00885、00893）永遠是「—」；你的 5 檔股票 9/23 換手率 0.19%～0.95%，不可能達到 high_turnover_surge 的 5%；私募比重高的公司換手率偏低（例如 2434：app 8.13%、第三方 17.69%）；資訊列換手率將來補上流通股數時會放大 100 倍。
- **finding**：`A06-turnover-mcap-pct#6`（P3△）、`A06-turnover-mcap-pct#8`（P3✓）、`A09-strategies-momentum#9`（P3✓）、`G05-gap#10`（P3✓）

#### RC30｜布林通道用樣本標準差（ddof=1）

- **最高嚴重度**：P3（P3×4）｜**狀態**：確認成立 1、部分成立 3｜**性質**：單一根因（四章各報一次）｜**涉及章**：A05、A08、A09、G04
- **說明**：布林通道在 enriched、策略矩陣與關鍵價位都用樣本標準差，而 Bollinger 原定義是母體標準差，σ 因此放大約 1.026 倍（盤中增量路徑反而用母體，見 RC09）。
- **對你的影響**：上下軌比多數看盤軟體略寬（2454 9/14 上軌差 0.39%）；全市場突破上軌的訊號少約 8%，9/23 boll_breakout 因此少選 00897、6205；7/1～9/23 你的自選股有 1 筆突破、3 筆跌破，在母體定義下會觸發，實際沒有觸發。
- **finding**：`A05-limits-signals#10`（P3△）、`A08-strategies-trend#6`（P3△）、`A09-strategies-momentum#8`（P3△）、`G04-gap#6`（P3✓）

#### RC31｜以 252 日年化、以自然日代替交易日

- **最高嚴重度**：P3（P3×4）｜**狀態**：確認成立 2、部分成立 2｜**性質**：同類歸併｜**涉及章**：A04、A09、A11
- **說明**：年化與窗口沒有依台股交易日曆：`annual_vol_20d` 與回測的年化、Sharpe 固定用 252（台股 2024 年 242 天、2025 年 243 天），回測另有 Sharpe 未扣無風險利率等統計口徑問題，步進優化的 252／63 以自然日計算。
- **對你的影響**：年化波動高估約 2%，low_volatility_leader 的 30% 門檻實際約嚴 2%；回測（目前只能經 API）的年化報酬與 Sharpe 絕對值被放大約 2%～4%（虧損時則是誇大虧損），步進優化的樣本比預期少約 1/3。
- **finding**：`A04-boll-atr-vol#8`（P3△）、`A09-strategies-momentum#10`（P3✓）、`A11-backtest-costs#6`（P3△）、`A11-backtest-costs#7`（P3✓）

#### RC32｜數值邊界：浮點相等、0/0、先捨入再比門檻

- **最高嚴重度**：P3（P3×4）｜**狀態**：部分成立 4｜**性質**：同類歸併｜**涉及章**：A02、A03、A05、G01
- **說明**：數值邊界沒有處理：收盤恰等於均線時交叉與突破由浮點殘差決定，RSV 分母為 0 產生的 NaN 讓 KD 永久變 null（潛在），異常診斷先四捨五入再比門檻。
- **對你的影響**：全市場歷史約 1,457 筆均線訊號與精確結果不同（多為早一天，或多觸發、漏觸發一次），你的 9 檔在全部歷史中差 0 筆；異常清單有 92 筆因先捨入而多觸發（約 0.3%）；KD 的 0/0 在目前資料中 0 例。
- **finding**：`A02-ma-macd#8`（P3△）、`A03-kd-rsi#6`（P3△）、`A05-limits-signals#12`（P3△）、`G01-gap#5`（P3△）

#### RC33｜法人／融資「缺列」的語意不一致

- **最高嚴重度**：P3（P3×3）｜**狀態**：部分成立 3｜**性質**：單一根因｜**涉及章**：A07、G01
- **說明**：T86 只列出當日有法人交易的證券，但各處對「缺列」的解讀不一：詳細頁退回前一日資料並把缺列說成「尚未回補」、選股器取每檔自己最新的一列（可能是數月前）、異常診斷基期以有列的日數為分母（專案 B9 的慣例是缺列＝0）。
- **對你的影響**：你的自選股不受影響。選股器有 140 列法人資料早於 9/23，最舊到 2026-04-14（約 5 個月前），卻以全域日期「法人：2026-09-23」標示並被當成當前值篩選；詳細頁對法人交易稀疏的個股會顯示前一交易日的數字；冷門股的外資突襲買超可能漏報。
- **finding**：`A07-chips#3`（P3△）、`A07-chips#4`（P3△）、`G01-gap#10`（P3△）

#### RC34｜缺資料被標成「不適用」、品質標示寫死

- **最高嚴重度**：P3（P3×4）｜**狀態**：確認成立 3、部分成立 1｜**性質**：同類歸併｜**涉及章**：G02、G03
- **說明**：資料可用性的標示不可信：比較頁把所有 null 顯示成「N/A（不適用）」（個股本益比其實是刻意 fail-closed 的缺資料）、研究卡證據計數寫死且 monitor／realtime 脈絡標為 KNOWN、融資 21:40 才更新，使 16:30～21:40 的 AI 證據整段丟掉大盤與產業脈絡。
- **對你的影響**：比較 2454、3034、2379 等個股時，本益比列永遠是「N/A（不適用）」，把「沒有接上資料」說成「不適用」；研究卡的品質標示不可信（上櫃 9/23 價格缺失時仍標 complete）；16:30～21:40 之間手動產生的 AI 報告缺少大盤與產業解讀。
- **finding**：`G02-gap#0`（P3△）、`G03-gap#6`（P3✓）、`G03-gap#7`（P3✓）、`G03-gap#9`（P3✓）

#### RC35｜成交量門檻以固定股數計

- **最高嚴重度**：P3（P3×2）｜**狀態**：部分成立 2｜**性質**：單一根因｜**涉及章**：A08、A10
- **說明**：swing_trend_confirmation 與 pullback_resume 以固定 2,000,000 股為流動性門檻，換算成交值在不同股價之間相差數百倍（驗證者指出 pullback_resume 是忠實移植 tw-stock-radar 的「2000 張」規則，不是沿用 A 股）。
- **對你的影響**：6196、3545、00893 幾乎不可能觸發「強勢回檔再起漲」；高價權值股（例如 3653、2059、5274、6669）在波段策略中被系統性濾掉，成交值很小的低價股反而通過。
- **finding**：`A08-strategies-trend#11`（P3△）、`A10-strategies-reversal#8`（P3△）

#### RC36｜進場訊號一律標成 MA5/MA20 黃金交叉

- **最高嚴重度**：P3（P3×2）｜**狀態**：確認成立 2｜**性質**：單一根因｜**涉及章**：A08、A10
- **說明**：bullish_alignment、pullback_ma20_bounce、pullback_to_support 的進場訊號都掛 `signal_ma_golden_5_20`，但它們的進場條件不是黃金交叉事件。
- **對你的影響**：回測紀錄的進場歸因錯誤；你目前沒有監控規則，之後啟用策略監控時，buy_signal 告警文字會寫成黃金交叉。
- **finding**：`A08-strategies-trend#10`（P3✓）、`A10-strategies-reversal#7`（P3✓）

### 2.3 其他單項問題

| finding | 嚴重度（原→終） | 狀態 | 問題 | 對你的影響 |
|---|---|---|---|---|
| `A07-chips#1` | P2→P2 | 確認成立 | 個股詳細頁的即時抓取路徑不計算券資比（融資 adapter 固定給 None，只有 store 備援路徑有值） | 你開著即時行情，打開任何自選股詳細頁，「券資比」與「最新券資比因子」都是「--」 |
| `A01-price-data#6` | P3→P3 | 部分成立 | enriched 歷史分區欄位不一致：661 個分區中 651 個只有 65 欄，缺漲跌停訊號、換手率與連板數；模組 docstring 已過時 | 以歷史日期查漲跌停、換手率或連板數（回測、歷史選股）只會拿到 null |
| `A01-price-data#9` | P3→P3 | 確認成立 | 逐檔路徑（CLI `--symbols`）的上櫃量以張×1000 近似且不含鉅額，與日常快照路徑口徑不同 | 只有手動使用 `--symbols` 時才會發生 |
| `A07-chips#5` | P3→P3 | 確認成立 | `factors.py` 的 `compute_chip_factors`、`compute_margin_factors` 只被測試呼叫，語意也有誤 | 目前沒有畫面使用；將來接上會產生錯誤因子 |
| `A12-screener-stats#6` | P3→P3 | 確認成立 | 首頁「策略當日推薦 552 檔」重複加總各策略的命中數，不重複的股票只有 290 檔 | 首頁候選股數高估約 90% |
| `A13-realtime#5` | P3→P3 | 確認成立 | Yahoo 備援把當日最高價當成開盤價 | 只在 MIS 缺某檔、改走 Yahoo 時出現；自選清單與個股頁會顯示錯的開盤價 |
| `A12-screener-stats#10` | P3→P3（驗證者判為 none） | 被推翻 | 見第 4 節 | 不需修正 |

## 3. 統計表

數字直接取自 `coverage.json`。`coverage.json` 沒有「有爭議」（disputed）與「未驗證」（unverified）兩個鍵，表中記為 0；兩者的判定方式見 5.3。「確認正確」是稽核後確認沒有問題的項目，「無法驗證」是查不到可比對依據的項目，兩者都不計入 finding 數。

| 章 | 章名 | finding | 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|---|---|---|
| A01 | 股價與成交資料正確性 | 10 | 4 | 6 | 0 | 0 | 0 | 14 | 9 |
| A02 | 均線、EMA 與 MACD | 9 | 4 | 5 | 0 | 0 | 0 | 9 | 5 |
| A03 | KD/KDJ 與 RSI | 7 | 4 | 3 | 0 | 0 | 0 | 15 | 5 |
| A04 | 布林、ATR、波動率、量比、量均、動能、區間高低 | 12 | 5 | 7 | 0 | 0 | 0 | 14 | 7 |
| A05 | 漲跌停、檔位與所有 signal_* 訊號 | 15 | 7 | 8 | 0 | 0 | 0 | 11 | 9 |
| A06 | 換手率、市值、股本、漲跌幅、振幅、成交值與顯示單位 | 12 | 7 | 5 | 0 | 0 | 0 | 12 | 7 |
| A07 | 三大法人、融資融券與籌碼因子 | 7 | 5 | 2 | 0 | 0 | 0 | 17 | 6 |
| A08 | 策略合理性（趨勢與突破類） | 12 | 2 | 10 | 0 | 0 | 0 | 14 | 7 |
| A09 | 策略合理性（量價、動能、因子與機器學習類） | 12 | 5 | 7 | 0 | 0 | 0 | 21 | 7 |
| A10 | 策略合理性（反轉與回檔類）與策略評分框架 | 16 | 9 | 7 | 0 | 0 | 0 | 18 | 6 |
| A11 | 回測、交易成本與交易規則 | 11 | 8 | 3 | 0 | 0 | 0 | 15 | 7 |
| A12 | 選股與市場統計 | 11 | 3 | 7 | 0 | 1 | 0 | 14 | 9 |
| A13 | 盤中即時與分鐘資料 | 8 | 6 | 2 | 0 | 0 | 0 | 18 | 9 |
| G01 | G1-abnormal-diagnostics（異常異動診斷） | 12 | 5 | 7 | 0 | 0 | 0 | 15 | 6 |
| G02 | G2-fundamentals-etf-reference（基本面與 ETF 參考資料） | 6 | 4 | 2 | 0 | 0 | 0 | 16 | 9 |
| G03 | G3-research-context-compare-detail（研究脈絡、比較頁與詳細頁） | 10 | 9 | 1 | 0 | 0 | 0 | 14 | 8 |
| G04 | G4-key-price-levels（關鍵價位） | 11 | 7 | 4 | 0 | 0 | 0 | 12 | 5 |
| G05 | G5-frontend-chart-calcs（前端自算的圖表數值） | 11 | 11 | 0 | 0 | 0 | 0 | 14 | 5 |
| G06 | G6-industry-classification（產業分類代碼對照） | 3 | 3 | 0 | 0 | 0 | 0 | 9 | 5 |
| 合計 | | 195 | 108 | 86 | 0 | 1 | 0 | 272 | 131 |

嚴重度分布（取自 `findings_index.json` 的 final_severity，不含被推翻的 1 條）：P1 27、P2 64、P3 103，沒有 P0。

## 4. 被推翻的項目

全部 195 條中只有 1 條被推翻，沒有「有爭議」（一位確認、一位推翻）的項目。

**`A12-screener-stats#10`：自選維度成員彈窗的平盤數把沒有行情的成員也算進去**（原 P3，由數據重現驗證者推翻）

- **原主張**：`flat` 以「成員數減去非 0 漲跌的筆數」計算，漲跌幅為 null 的成員被算成平盤，平盤家數被高估。
- **推翻理由**：
  - 計算方式可以重現，但這一格在畫面上的欄位名稱是「平盤/待更新」（`DimensionMembersDialog.tsx:217`），本來就是「0 漲跌＋無行情」的合計。
  - 上漲、下跌、平盤/待更新三欄加總恆等於成員總數；finding 舉的例子（3 漲、2 跌、1 平、4 缺值）算出 5，正是正確值。
  - 照 finding 的修法改，待更新的成員反而會從統計中消失，欄位名稱也會變錯。
  - 此部署沒有擴充設定（`data/ext_data` 不存在，finding 誤寫為 `data/ext`），這個彈窗目前不可達。
- **索引註記**：`findings_index.json` 的 final_severity 仍記 P3。原因是 `build_sections.py` 只採用 P0～P3 的修正後嚴重度，而驗證者給的是 none。

## 5. 方法與限制

### 5.1 官方參考資料

- 由單一 agent 從證交所與櫃買中心下載；其他 agent 一律不得直接連交易所，只能用這批資料比對。章節檔中可見「依規定未直連」等註記，官方網頁的內容是透過搜尋摘要或 Wayback 封存間接引用。
- 依 `reference/manifest.json`：
  - 取得時間：2026-09-24 01:49～01:57（台北）。
  - 請求：共 43 次（www.twse.com.tw 28、www.tpex.org.tw 12、openapi.twse.com.tw 2、mis.twse.com.tw 1），0 次失敗。
  - 節奏：每兩次請求至少間隔 6.5 秒（所有主機合計），逾時 20 秒，失敗只重試 1 次。
- 抽樣內容與期間（`coverage.json` 的 reference.fetched，共 14 組、0 組失敗）：
  - 上市月日K：2330、2454、3034、2379、6196、3545、0050、00878、00885、00893 共 10 檔，2026-08 與 2026-09（到 9/23），380 列。
  - 上櫃月日K：6488、8069，2026-08 與 2026-09，76 列。
  - 加權指數與櫃買指數：2026-08 與 2026-09，76 列。
  - 三大法人、融資融券：只有 9/22 與 9/23。
  - 全市場收盤行情：只有 9/23（上市 MI_INDEX、上櫃 dailyQuotes）。
  - 股本：上市出表日 9/22、上櫃 9/23；另有上市 ETF 基本資料（t187ap47_L）。
  - 上市除權除息計算結果表（TWT49U）：2026-06-01～09-23。上櫃的除權息表沒有下載。
  - MIS 快照 5 檔：收盤後取得，內容是 9/23 的最終狀態。
- manifest 自述的限制：最新交易日是 9/23；TPEx 部分端點在日期格式不符時會默默回傳最新資料，9/23 的請求無法用回應日期分辨這種情況。

### 5.2 稽核範圍

- 13 個領域（A01～A13），加上完整性檢查補查的 6 個領域（G01～G06，主題見 `coverage.json` 的 critic_gaps）：
  - 選股頁的異常異動診斷。
  - 個股詳細頁的基本面與 ETF 參考資料。
  - 研究脈絡與比較頁中 5 日口徑以外的欄位。
  - 價格提醒對話框的 11 類關鍵價位。
  - 前端自算的圖表數值。
  - 產業分類代碼對照。
- 完整性檢查的結論（`coverage.json` 的 critic_assessment）：13 個領域已涵蓋 enriched 的全部欄位與 21 支內建策略。刻意不列為缺口的有：
  - `backtest/factor.py` 的 IC／IR 與 mining：前端沒有頁面。
  - sector_monitor、monitor_rules、custom_signals：監控規則 0 條，相關目錄是空的。
  - screener_nl：由 LLM 換算單位。
  - `dividend_events.py`：沒有進入任何畫面。

### 5.3 驗證方式

- **驗證角度**：依原始嚴重度分派（`build_sections.py` 的 lenses_for）。
  - 原為 P1～P2 的 85 條：由「定義」（definition）與「數據重現」（reproduction）兩個角度各一位 agent 獨立驗證。
  - 原為 P3 的 110 條：只由「數據重現」一位驗證。
  - 本次沒有 P0。
  - 因此驗證後由 P3 升為 P2 的 8 條只有一位驗證者：`A01-price-data#8`、`A04-boll-atr-vol#10`、`A09-strategies-momentum#7`、`A10-strategies-reversal#13`、`A12-screener-stats#7`、`A13-realtime#6`、`G01-gap#6`、`G05-gap#5`。
- **狀態判定**：
  - 所有驗證者都判確認，才算確認成立；全部判推翻才算被推翻。
  - 一位確認、一位推翻算有爭議；其餘算部分成立。
  - 沒有任何驗證結果算未驗證。
- **最終嚴重度**：取驗證者修正後嚴重度中最嚴重的一個。
  - 升級 12 條：P2→P1 4 條（`A04-boll-atr-vol#4`、`A08-strategies-trend#2`、`A09-strategies-momentum#5`、`A12-screener-stats#3`）；P3→P2 8 條（見上）。
  - 降級 2 條：P2→P3（`A02-ma-macd#4`、`A11-backtest-costs#4`）。
- **取最新一次成功的驗證**：同一個驗證 prompt 曾執行兩次，第二次因用量上限中斷，只有部分項目重跑成功。`build_sections.py` 對每個標籤取工作流程紀錄中最後一筆結果。`coverage.json` 的 missing_verdicts 為空，表示每條 finding 應有的驗證角度都有結果。
- **章節檔的產生方式**：由 `build_sections.py` 直接從工作流程紀錄轉換，未經模型改寫。原流程中撰寫各章（section:A01～A13）與總報告（report、report-summary）的 15 個步驟沒有產出結果（`coverage.json` 的 failed_labels_without_result）。

### 5.4 限制

- 無法驗證的共 131 項，逐項列在各章的「無法驗證的項目」，每章數量見第 3 節。常見原因：
  - 第三方技術指標頁由前端動態產生，或回 403。
  - 規定不得連官方，所以 app 即時抓官方資料才顯示的數值（例如 5 檔自選股的本益比、殖利率）無法取得。
  - 參考資料只有 9/23 的 MIS 快照，無法確認除權息當日 MIS 的昨收。
  - 上櫃除權息表沒有下載。
- A07 以後的 13 章都有 WebSearch 額度用完的註記，其中 11 章明寫 200/200。部分線上定義改用 WebFetch 查證，或沒有做線上核實，例如「新上市前 5 日無漲跌幅」就沒能上網核實。
- 官方參考資料是抽樣：月日K只有 12 檔、法人與融資只有 2 天、全市場收盤只有 1 天。涉及更長期間或全市場的統計（例如 956 次除權息事件、2024-01 起的一字漲跌停次數），主要以本地 parquet 重算，規則與抽樣數值再用 TWT49U 等官方參考資料校對。
- `findings_index.json` 的主張與修正後敘述截斷在 400 字、使用者影響截斷在 300 字；完整內容以章節檔為準。

### 5.5 本總覽的限制

- 由單一 agent 依上述輸入整理，沒有重跑驗證、沒有讀專案程式碼、沒有網路請求。
- 根因歸併、「性質」與排序屬整理者判斷；每條 finding 只歸一個主要根因，跨根因者以「另見」標註。
- 「對你的影響」依各 finding 的使用者影響、驗證者修正後敘述與各章稽核者摘要改寫。自選 9 檔的除息日期取自 manifest 的 watchlist_coverage。

## 6. 各章檔案索引

| 代號 | 檔案 | 章名 |
|---|---|---|
| A01 | [sections/A01-price-data.md](sections/A01-price-data.md) | 股價與成交資料正確性 |
| A02 | [sections/A02-ma-macd.md](sections/A02-ma-macd.md) | 均線、EMA 與 MACD |
| A03 | [sections/A03-kd-rsi.md](sections/A03-kd-rsi.md) | KD/KDJ 與 RSI |
| A04 | [sections/A04-boll-atr-vol.md](sections/A04-boll-atr-vol.md) | 布林、ATR、波動率、量比、量均、動能、區間高低 |
| A05 | [sections/A05-limits-signals.md](sections/A05-limits-signals.md) | 漲跌停、檔位與所有 signal_* 訊號 |
| A06 | [sections/A06-turnover-mcap-pct.md](sections/A06-turnover-mcap-pct.md) | 換手率、市值、股本、漲跌幅、振幅、成交值與顯示單位 |
| A07 | [sections/A07-chips.md](sections/A07-chips.md) | 三大法人、融資融券與籌碼因子 |
| A08 | [sections/A08-strategies-trend.md](sections/A08-strategies-trend.md) | 策略合理性（趨勢與突破類） |
| A09 | [sections/A09-strategies-momentum.md](sections/A09-strategies-momentum.md) | 策略合理性（量價、動能、因子與機器學習類） |
| A10 | [sections/A10-strategies-reversal.md](sections/A10-strategies-reversal.md) | 策略合理性（反轉與回檔類）與策略評分框架 |
| A11 | [sections/A11-backtest-costs.md](sections/A11-backtest-costs.md) | 回測、交易成本與交易規則 |
| A12 | [sections/A12-screener-stats.md](sections/A12-screener-stats.md) | 選股與市場統計 |
| A13 | [sections/A13-realtime.md](sections/A13-realtime.md) | 盤中即時與分鐘資料 |
| G01 | [sections/G01-gap.md](sections/G01-gap.md) | 異常異動診斷（G1-abnormal-diagnostics） |
| G02 | [sections/G02-gap.md](sections/G02-gap.md) | 個股詳細頁的基本面與 ETF 參考資料（G2-fundamentals-etf-reference） |
| G03 | [sections/G03-gap.md](sections/G03-gap.md) | 研究脈絡、比較頁與詳細頁（G3-research-context-compare-detail） |
| G04 | [sections/G04-gap.md](sections/G04-gap.md) | 關鍵價位（G4-key-price-levels） |
| G05 | [sections/G05-gap.md](sections/G05-gap.md) | 前端自算的圖表數值（G5-frontend-chart-calcs） |
| G06 | [sections/G06-gap.md](sections/G06-gap.md) | 產業分類代碼對照（G6-industry-classification） |

稽核暫存目錄的其他內容（沒有納入版本庫）：`findings_index.json`（195 條 finding 的精簡索引）、`coverage.json`（各章統計、完整性檢查與參考資料清單）、`reference/`（官方參考資料，`manifest.json` 為清單）、`work/`（各 agent 的腳本與中間結果）、`build_sections.py`（由工作流程紀錄產生章節檔、索引與 coverage.json 的腳本）。

## 附錄：finding 對照表

依章與序號排列。「根因」欄對應第 2 節的編號；標題取自 `findings_index.json`，可以直接在對應的章節檔中搜尋。

| finding | 根因 | 嚴重度（原→終） | 狀態 | 標題 |
|---|---|---|---|---|
| `A01-price-data#0` | RC02 | P1→P1 | 確認成立 | F1 上櫃抓取失敗時仍寫入只有上市的分區，並視為當日完成；9/16、9/23 整個上櫃市場缺漏且不會自動補回 |
| `A01-price-data#1` | RC01 | P1→P1 | 確認成立 | F2 台股完全沒有除權息調整：除權息日的 change_pct 以除權息前收盤計算，指標與訊號跨除權息缺口失真 |
| `A01-price-data#2` | RC01 | P2→P2 | 部分成立 | F3 除權息日的漲跌停價以除權息前收盤計算，而不是除權息參考價，造成漲停漏判與跌停誤判 |
| `A01-price-data#3` | RC03 | P2→P2 | 部分成立 | F4 16:30 當日抓取的上市成交量／金額偏低（缺少稍後才併入的量），之後永遠不會重抓 |
| `A01-price-data#4` | RC22 | P2→P2 | 確認成立 | F5 data_integrity 只檢查台股部署不寫入的 A 股表：看不到台股資料缺漏，還會誤報並觸發空轉的修復任務，讓開啟即時行情時回 409 |
| `A01-price-data#5` | RC22 | P3→P3 | 部分成立 | F6 data/kline_daily 殘留日期錯置的舊列（B3 殘留），仍被資料狀態統計與 data_integrity 讀取 |
| `A01-price-data#6` | 單項 | P3→P3 | 部分成立 | F7 enriched 歷史分區欄位不一致，且 enriched_refresh 的 docstring 與實作矛盾 |
| `A01-price-data#7` | RC02 | P3→P3 | 部分成立 | F8 快照解析器不比對回應本身的日期，一律以請求日期標記 |
| `A01-price-data#8` | RC14 | P3→P2 | 部分成立 | F9 創新板股票 7812 稜研科技不在證券主檔，沒有任何日K |
| `A01-price-data#9` | 單項 | P3→P3 | 確認成立 | F10 逐檔路徑（official_provider）的上櫃量以張×1000 近似且不含鉅額，與快照路徑口徑不同 |
| `A02-ma-macd#0` | RC01 | P1→P1 | 部分成立 | 台股 enriched 用未還原價：分割與除權後 MA／EMA／MACD 大幅失真並觸發假訊號 |
| `A02-ma-macd#1` | RC09 | P2→P2 | 部分成立 | 盤中增量 live_agg 用 90 個日曆天代替 60 個交易日：2–5 月 MA60 低估 7–9%，新上市股的 MA 算出錯值 |
| `A02-ma-macd#2` | RC09 | P2→P2 | 確認成立 | 盤中增量 MACD 混用兩種暖機長度，DIF 誤差最高約 5 點並翻轉金叉、死叉 |
| `A02-ma-macd#3` | RC09 | P2→P2 | 確認成立 | 盤中交叉訊號從第二輪輪詢起拿上一輪快照比，而不是拿昨收比 |
| `A02-ma-macd#4` | RC16 | P2→P3 | 部分成立 | MACD 柱狀體用中國慣例 2×(DIF−DEA)，並標成「MACD」，與台灣慣例（DIF／MACD／OSC）不符 |
| `A02-ma-macd#5` | RC09 | P3→P3 | 確認成立 | 盤中全量回退路徑讀 kline_daily（台股部署幾乎是空的），啟動預熱期間活蠟燭的 MA／MACD 錯誤 |
| `A02-ma-macd#6` | RC25 | P3→P3 | 部分成立 | 全量 EMA／MACD 沒有暖機門檻，與自選頁路徑的 35 根門檻不一致 |
| `A02-ma-macd#7` | RC05 | P3→P3 | 確認成立 | 無成交日（價格為 null 的列）在不同路徑處理不同：自選頁與選股器的 MA 會變 null 最多 60 天 |
| `A02-ma-macd#8` | RC32 | P3→P3 | 部分成立 | 收盤價恰好等於均線時，突破／跌破訊號取決於浮點殘差 |
| `A03-kd-rsi#0` | RC01 | P1→P1 | 部分成立 | 台股 KD／RSI 以未還原分割的價格計算，0050 一拆四後出現長達數月的假超賣 |
| `A03-kd-rsi#1` | RC05 | P2→P2 | 確認成立 | 台股選股器、個股比較頁與 AI 研究的「RSI(14)」實為 Cutler 簡單平均，與全站 Wilder RSI14 最多差 13 點 |
| `A03-kd-rsi#2` | RC25 | P2→P2 | 部分成立 | pipeline 的 RSI／KD 沒有暖機門檻：上市首日 RSI 一律為 0，KD 也沒有台灣慣例的初值 50 |
| `A03-kd-rsi#3` | RC09 | P3→P3 | 確認成立 | 盤中增量 RSI 的遞推狀態只用 90 日曆天計算，RSI24 與收盤後全量值最多差 2.95 點 |
| `A03-kd-rsi#4` | RC25 | P3→P3 | 確認成立 | K 線自訂起日落在 300 日快取前段時沒有暖機，首批 K 棒的 RSI 與 KD 大幅失真 |
| `A03-kd-rsi#5` | RC05 | P3→P3 | 確認成立 | 自選頁台股列的 KDJ、RSI6、RSI24 永遠為「—」，且 RSI14 在盤中是前一交易日的值 |
| `A03-kd-rsi#6` | RC32 | P3→P3 | 部分成立 | （潛在）RSV 分母為 0 時產生 NaN，全量 KD 之後永久變成 null，且與回測矩陣路徑的結果不一致 |
| `A04-boll-atr-vol#0` | RC01 | P1→P1 | 確認成立 | 台股 enriched 指標以未還原價計算：除權息與 0050 分割讓動能、波動率、ATR、布林、60 日高低嚴重失真 |
| `A04-boll-atr-vol#1` | RC09 | P2→P2 | 確認成立 | 盤中增量路徑的 high_60d/low_60d 改用最高／最低價，與盤後收盤價口徑不同，導致「創60日新高」盤中幾乎不觸發 |
| `A04-boll-atr-vol#2` | RC09 | P2→P2 | 確認成立 | 盤中增量路徑的布林與年化波動用母體標準差、全量用樣本標準差，且突破比較子一邊是 >= 一邊是 > |
| `A04-boll-atr-vol#3` | RC09 | P2→P2 | 部分成立 | 盤中遞推狀態取「日曆 90 天」，春節後常不足 60 個交易日，momentum_60d、ma60、60 日高低全錯 |
| `A04-boll-atr-vol#4` | RC05 | P2→P1 | 部分成立 | 台股選股器與研究脈絡的 vol_ratio_5d 分母含當日，與管線（前 5 日）同名不同義 |
| `A04-boll-atr-vol#5` | RC10 | P3→P3 | 部分成立 | 盤中量比分子（MIS 盤中整股量）與分母（官方全日量，含零股、盤後定價、鉅額）範圍不一致，收盤時系統性低估 5～8% |
| `A04-boll-atr-vol#6` | RC10 | P3→P3 | 確認成立 | session_progress 以秒計，開盤後數秒量比放大上千倍，signal_volume_surge 幾乎必然觸發 |
| `A04-boll-atr-vol#7` | RC25 | P3→P3 | 部分成立 | ATR 以第一根 H−L 為 EWM 種子且無 min_samples，暖機期（約前 40～60 根）與 Wilder 定義偏差最高 27% |
| `A04-boll-atr-vol#8` | RC31 | P3→P3 | 部分成立 | annual_vol_20d 以 √252 年化並使用簡單報酬；台股實際每年 242～243 個交易日，教科書定義用對數報酬 |
| `A04-boll-atr-vol#9` | RC05 | P3→P3 | 部分成立 | 台股自選清單與選股器的指標沒有過濾停牌空列，且盤中顯示昨收時點的動能 |
| `A04-boll-atr-vol#10` | RC03 | P3→P2 | 部分成立 | app daily store 近期成交量少於官方，影響 vol_ratio_5d 與 vol_ma5 |
| `A04-boll-atr-vol#11` | RC09 | P3→P3 | 確認成立 | 增量失敗時的全量回退以 kline_daily 為 90 天歷史；台股該表只有 4 個分區，且每個分區都是前一交易日的資料 |
| `A05-limits-signals#0` | RC06 | P1→P1 | 部分成立 | 回測的漲跌停鎖定用 A 股 0.01 元四捨五入，台股漲停鎖定大多偵測不到 |
| `A05-limits-signals#1` | RC06 | P2→P2 | 部分成立 | 已儲存的 consecutive_limit_ups/downs（連續漲停/跌停）與同一列的 signal_limit_up 不一致 |
| `A05-limits-signals#2` | RC01 | P2→P2 | 部分成立 | 除權息日以前一日收盤價當漲跌停基準，產生假跌停並漏判真漲跌停 |
| `A05-limits-signals#3` | RC12 | P2→P2 | 確認成立 | 新上市普通股前 5 個交易日仍套 10% 漲跌幅，產生假漲停與假跌停 |
| `A05-limits-signals#4` | RC14 | P2→P2 | 確認成立 | ETF 分類被拒時拋出的 ValueError 沒有被攔截：搜尋「台灣」、個股詳細頁、報價面板回應 500 |
| `A05-limits-signals#5` | RC14 | P2→P2 | 確認成立 | 櫃買 CFI「CEOGEU」被當成國內成分，4 檔國外成分 ETF 被賦予 10% 漲跌幅 |
| `A05-limits-signals#6` | RC19 | P2→P2 | 確認成立 | 台股選股器「逼近漲停 (≤3%)」永遠不會命中：以今日收盤當漲跌停基準 |
| `A05-limits-signals#7` | RC17 | P2→P2 | 確認成立 | 「跌停翻紅」標籤與實際定義不符（多數收黑），兩條路徑的定義也不同 |
| `A05-limits-signals#8` | RC12 | P2→P2 | 確認成立 | 分時圖與報價面板的漲跌停顯示錯誤：國外 ETF 仍畫 ±10% 線、進位用 0.01 元、百分比顯示成 0.1% |
| `A05-limits-signals#9` | RC09 | P3→P3 | 確認成立 | 盤中增量路徑與盤後路徑的 60 日新高／新低、布林、MA60 定義不一致 |
| `A05-limits-signals#10` | RC30 | P3→P3 | 部分成立 | 布林通道用樣本標準差（ddof=1），與 Bollinger 原定義（母體）不同 |
| `A05-limits-signals#11` | RC25 | P3→P3 | 部分成立 | MACD 交叉在暖機期（不足 33 根）就觸發 |
| `A05-limits-signals#12` | RC32 | P3→P3 | 部分成立 | 均線交叉與突破沒有處理浮點相等，交叉日期由累加誤差決定 |
| `A05-limits-signals#13` | RC06 | P3→P3 | 部分成立 | 10000 哨兵值會把 ≥10000 元的台股誤當成無漲跌幅（潛在） |
| `A05-limits-signals#14` | RC06 | P3→P3 | 部分成立 | 千金股升降單位將由 5 元改為 1 元，程式中的三份檔位表都沒有日期維度 |
| `A06-turnover-mcap-pct#0` | RC01 | P1→P1 | 確認成立 | 台股 enriched 未做除權息調整：除權息日漲跌幅、漲跌額、振幅以原始前一日收盤價計算 |
| `A06-turnover-mcap-pct#1` | RC03 | P1→P1 | 部分成立 | 16:30 排程早於鉅額交易收盤（17:00），抓到的日成交量與成交值偏少，而且永不重抓 |
| `A06-turnover-mcap-pct#2` | RC02 | P1→P1 | 確認成立 | 上櫃抓取失敗時仍寫入只有上市的分區並回報 success，之後永不補抓（9/16、9/23 整個上櫃市場缺日） |
| `A06-turnover-mcap-pct#3` | RC10 | P2→P2 | 部分成立 | 自選頁與盤中、收盤後當晚的換手率分子用 MIS 盤中整股量，與官方及第三方的總成交股數口徑不一致 |
| `A06-turnover-mcap-pct#4` | RC11 | P2→P2 | 確認成立 | 非交易日或隔日補跑的重新發佈，會把最近一個交易日的換手率清空 |
| `A06-turnover-mcap-pct#5` | RC02 | P3→P3 | 部分成立 | 股本刷新時，一個交易所失敗就整檔覆寫，另一交易所的股本暫時消失 |
| `A06-turnover-mcap-pct#6` | RC29 | P3→P3 | 部分成立 | 換手率分母含尚未上市的私募股，與第三方口徑不同，私募比重高的公司被低估 |
| `A06-turnover-mcap-pct#7` | RC27 | P3→P3 | 確認成立 | TDR 的分母與市值用外國原股總數，不是在台 TDR 單位數 |
| `A06-turnover-mcap-pct#8` | RC29 | P3→P3 | 確認成立 | ETF 換手率與市值恆為空（自選 9 檔中有 4 檔 ETF），第三方有提供 ETF 週轉率 |
| `A06-turnover-mcap-pct#9` | RC16 | P3→P3 | 確認成立 | fmtBigNum 以「萬億」表示 10^12，台灣慣用「兆」 |
| `A06-turnover-mcap-pct#10` | RC16 | P3→P3 | 部分成立 | 成交量以「股」加萬／億顯示，且不標單位；台股慣例以張為單位 |
| `A06-turnover-mcap-pct#11` | RC05 | P3→P3 | 確認成立 | 自選頁台股列的振幅恆為「—」，成交額在 MIS 為來源時恆為「—」 |
| `A07-chips#0` | RC03 | P1→P1 | 確認成立 | 三大法人在 16:30 寫入初版 T86 與上櫃資料後不再重抓，官方後續修正（含鉅額交易）永遠不會入庫 |
| `A07-chips#1` | 單項 | P2→P2 | 確認成立 | 個股詳細頁的即時抓取路徑不計算券資比：只要即時抓取成功，「券資比」與「最新券資比因子」一律是「--」 |
| `A07-chips#2` | RC04 | P2→P2 | 確認成立 | 市場層級法人買賣超用跨證券的股數加總，上櫃含權證、上市不含，與官方以金額計的口徑方向可能相反 |
| `A07-chips#3` | RC33 | P3→P3 | 部分成立 | 即時抓取時，若當日 T86 沒有該股，會退回顯示前一交易日的法人資料；歷史視窗把這種「0 的日子」說成「尚未回補」 |
| `A07-chips#4` | RC33 | P3→P3 | 部分成立 | 選股器用每檔自己的最新法人與融資列，把最多 5 週前的舊值當成當前值篩選；5 日欄位永遠是 null |
| `A07-chips#5` | 單項 | P3→P3 | 確認成立 | factors.py 的 compute_chip_factors 與 compute_margin_factors 是正式程式未使用、且語意有誤的程式 |
| `A07-chips#6` | RC17 | P3→P3 | 確認成立 | 「核心籌碼滾動因子」卡標示「近 5 日指標」，卻包含 1 日融資變動量與當日券資比 |
| `A08-strategies-trend#0` | RC02 | P1→P1 | 確認成立 | 9/23 的策略結果完全不含上櫃股：TPEx 快照失敗被吞掉，只有上市資料的分區被當成完整日，而且不會重抓 |
| `A08-strategies-trend#1` | RC24 | P2→P2 | 部分成立 | consolidation_breakout／swing 混用時間軸 rolling_max/min 與 valid_shift：缺一根 K 棒就在 20 根內完全不觸發，之後再用過期區間產生假訊號 |
| `A08-strategies-trend#2` | RC01 | P2→P1 | 部分成立 | 台股全程使用未還原價：除權息缺口在趨勢策略中大量產生假的出場與動能訊號 |
| `A08-strategies-trend#3` | RC25 | P2→P2 | 部分成立 | macd_golden 單獨執行（run_preset）只用 61 根暖機，約 18% 的金叉判定與全歷史 MACD 不同，而且會覆寫快取 |
| `A08-strategies-trend#4` | RC13 | P2→P2 | 部分成立 | ETF 的 turnover_rate 為 null，bullish_alignment 的評分因此被整筆歸 0（自選 0050、00878 排在後段） |
| `A08-strategies-trend#5` | RC25 | P3→P3 | 部分成立 | DEFAULT_BASIC_FILTER 的 exclude_new_days=30 從未實作 |
| `A08-strategies-trend#6` | RC30 | P3→P3 | 部分成立 | 布林帶使用樣本標準差（ddof=1），與 Bollinger 原作者的母體標準差不同 |
| `A08-strategies-trend#7` | RC17 | P3→P3 | 部分成立 | trend_breakout 的『60日新高』實際是『收盤價不低於近 60 根收盤（含當日）』，欄位標示為『60日最高价』；MA60 條件是多餘的 |
| `A08-strategies-trend#8` | RC23 | P3→P3 | 部分成立 | consolidation_breakout 的評分與參數與『低檔量縮整理』的前提相反或過於寬鬆 |
| `A08-strategies-trend#9` | RC23 | P3→P3 | 部分成立 | 突破類策略排名前段多為漲停鎖死、隔日難以依收盤買進，評分又加重 change_pct |
| `A08-strategies-trend#10` | RC36 | P3→P3 | 確認成立 | bullish_alignment 的進場命中被標示為『MA5上穿MA20（金叉）』，但條件其實是持續狀態 |
| `A08-strategies-trend#11` | RC35 | P3→P3 | 部分成立 | swing_trend_confirmation 的 min_volume 以股數計，門檻隨股價相差數百倍 |
| `A09-strategies-momentum#0` | RC01 | P1→P1 | 部分成立 | 台股 enriched 價格完全未還原除權息，動能與均線類策略在除權息日產生假訊號 |
| `A09-strategies-momentum#1` | RC25 | P2→P2 | 部分成立 | lorentzian_knn 的選股與回測在同一天給出不同訊號（候選池取決於歷史窗長度） |
| `A09-strategies-momentum#2` | RC23 | P2→P2 | 部分成立 | factor_rank_research 以截面 min-max 搭配固定的 70/40 門檻，結果被極端值主導，且與因子研究的 rank 口徑不一致 |
| `A09-strategies-momentum#3` | RC13 | P2→P2 | 部分成立 | 換手率類策略在沒有歷史換手率時靜默降級：high_turnover_surge 永遠 0 筆，low_volatility_leader 把空值偽裝成 0 分 |
| `A09-strategies-momentum#4` | RC24 | P2→P2 | 部分成立 | 原始列滾動窗口（rolling_quantile、rolling_min、rolling_max、rolling_sum）遇到缺列時，靜默抑制布林收斂與 KNN 訊號 |
| `A09-strategies-momentum#5` | RC03 | P2→P1 | 確認成立 | enriched／台股日線存檔的成交量低於官方數字，且 2026-09-23 的分區缺少全部上櫃資料 |
| `A09-strategies-momentum#6` | RC28 | P3→P3 | 確認成立 | composite 選股合併與回測合併在單一候選與同分時的分數口徑不同，且選股結果受雜湊種子影響 |
| `A09-strategies-momentum#7` | RC24 | P3→P2 | 部分成立 | lorentzian_knn 與 TradingView「Lorentzian Classification」原版有多處設計差異，且 38% 的近鄰含 0.5 佔位特徵 |
| `A09-strategies-momentum#8` | RC30 | P3→P3 | 部分成立 | 布林通道以樣本標準差（n−1）計算，與 Bollinger 規定的母體標準差（n）不同；收斂定義為自訂變體 |
| `A09-strategies-momentum#9` | RC29 | P3→P3 | 確認成立 | high_turnover_surge 的 5% 換手門檻沿用 A 股流通股慣例，台股以發行股數計算時只篩出極端的前 3% |
| `A09-strategies-momentum#10` | RC31 | P3→P3 | 確認成立 | annual_vol_20d 以 252 日年化，台股實際每年約 242～243 個交易日 |
| `A09-strategies-momentum#11` | RC25 | P3→P3 | 確認成立 | ema{N}_bias 在矩陣路徑從矩陣起點重新計算 EMA，忽略已載入的 emaN 欄位，結果隨窗長度漂移 |
| `A10-strategies-reversal#0` | RC01 | P1→P1 | 確認成立 | 台股價格未還原除權息，反轉與回檔策略把除權息缺口當成下跌，產生假訊號與漏訊號 |
| `A10-strategies-reversal#1` | RC13 | P2→P2 | 確認成立 | 矩陣評分遇到缺值給 0 分並留在結果中；pullback_to_support 的換手率幾乎全歷史缺值，候選幾乎全部 0 分 |
| `A10-strategies-reversal#2` | RC23 | P2→P2 | 部分成立 | bottom_reversal 的出場條件「收盤 < MA20」在進場當根就成立，實際上只持有一天 |
| `A10-strategies-reversal#3` | RC28 | P2→P2 | 確認成立 | composite 的選股與回測在單一候選與同分時歸一化不一致，同分排名還會隨 hash seed 改變 |
| `A10-strategies-reversal#4` | RC23 | P3→P3 | 部分成立 | 反轉類策略預設偏好高 RSI 與高 5 日動能，評分方向和「超跌反彈」邏輯相反 |
| `A10-strategies-reversal#5` | RC23 | P3→P3 | 確認成立 | bottom_reversal 的「爆量」只和前一天比，不是台股慣用的 5 日均量倍數 |
| `A10-strategies-reversal#6` | RC23 | P3→P3 | 部分成立 | n_day_low_reversal 要求「收盤就是 60 日最低收盤」，幾乎抓不到盤中破底後收回的反轉 |
| `A10-strategies-reversal#7` | RC36 | P3→P3 | 確認成立 | 兩支回檔策略的進場訊號被標成「MA5/MA20 黃金交叉」，但實際上很少是黃金交叉 |
| `A10-strategies-reversal#8` | RC35 | P3→P3 | 部分成立 | pullback_resume 的最低成交量用股數 2,000,000 股，與股價無關（A 股 2 萬手的沿用） |
| `A10-strategies-reversal#9` | RC23 | P3→P3 | 確認成立 | pullback_to_support 允許在跌破 MA20 當天進場，與自己的出場訊號同一根 |
| `A10-strategies-reversal#10` | RC14 | P3→P3 | 確認成立 | 限股票的策略也把台股 ETF 選進結果 |
| `A10-strategies-reversal#11` | RC25 | P3→P3 | 部分成立 | 暖機期不足：RSI 型進場用 60 根，EMA/MACD/ATR 類虛擬評分欄位的暖機只有 1 根 |
| `A10-strategies-reversal#12` | RC13 | P3→P3 | 確認成立 | 非矩陣（polars）評分路徑：缺值候選排在第一位，缺少的評分欄位被靜默剔除 |
| `A10-strategies-reversal#13` | RC25 | P3→P2 | 部分成立 | DEFAULT_BASIC_FILTER 的 exclude_new_days=30 從未實作 |
| `A10-strategies-reversal#14` | RC03 | P3→P3 | 部分成立 | 近期（9/16–9/23）本地成交量低於官方，量比 vol_ratio_5d 因此偏差（跨資料層，只報對策略的影響） |
| `A10-strategies-reversal#15` | RC28 | P3→P3 | 確認成立 | composite 的 union 模式：只被一支子策略命中的標的不受權重影響，也不獎勵多重確認 |
| `A11-backtest-costs#0` | RC07 | P1→P1 | 確認成立 | 回測與挖掘的預設交易成本是 A 股口徑，台股成本模型沒有接上 |
| `A11-backtest-costs#1` | RC06 | P1→P1 | 確認成立 | 回測的漲跌停鎖死判定用 A 股「0.01 四捨五入」算價，漏判大多數台股漲跌停 |
| `A11-backtest-costs#2` | RC01 | P1→P1 | 確認成立 | 台股回測使用未還原價格且不計股利，除權息缺口被當成虧損，並誤觸停損 |
| `A11-backtest-costs#3` | RC08 | P1→P1 | 確認成立 | minute_fill 的參考線（MA5）含成交當日收盤，形成未來函數 |
| `A11-backtest-costs#4` | RC07 | P2→P3 | 部分成立 | 正式撮合寫死每單位 100 股（A 股一手），不符台股整股 1000 股／零股 1 股 |
| `A11-backtest-costs#5` | RC07 | P3→P3 | 確認成立 | market_profile 的賣出稅率一律套用一般股 0.3%，ETF 與債券 ETF 被多收稅（潛在問題） |
| `A11-backtest-costs#6` | RC31 | P3→P3 | 部分成立 | 統計口徑：年化固定用 252 日、Sharpe 未扣無風險利率、profit_factor 以百分比加總、mc_maxdd 把每筆交易當成全額資金 |
| `A11-backtest-costs#7` | RC31 | P3→P3 | 確認成立 | 步進優化的 train/test/step 以自然日計算，預設 252/63 實際只有約 163～175／36～46 個交易日 |
| `A11-backtest-costs#8` | RC08 | P3→P3 | 確認成立 | close_t 進場時，移動停損與移動停利的峰值包含進場前的當日高點 |
| `A11-backtest-costs#9` | RC22 | P3→P3 | 確認成立 | 策略回測的基準曲線寫死上證指數（000001.SH） |
| `A11-backtest-costs#10` | RC07 | P3→P3 | 部分成立 | legacy simulate()／_calc_stats 是沒有正式呼叫端的舊路徑，統計公式錯誤；台股回測測試只覆蓋這條路徑 |
| `A12-screener-stats#0` | RC02 | P1→P1 | 確認成立 | 9/16 與 9/23 日K缺整個上櫃市場，市場統計、產業、選股、策略全部只剩上市，品質卻顯示 complete |
| `A12-screener-stats#1` | RC01 | P1→P1 | 部分成立 | 除權息日以未還原昨收計算漲跌，造成假下跌、假跌停，並扭曲漲跌幅分布、產業強弱與分組平均 |
| `A12-screener-stats#2` | RC19 | P2→P2 | 確認成立 | 選股「逼近漲停／跌停（≤3%）」篩選永遠 0 筆：漲跌停價以當日收盤計算，實際是隔日的漲跌停價 |
| `A12-screener-stats#3` | RC04 | P2→P1 | 部分成立 | 全市場法人買賣超以「股數加總」呈現，混入 ETF 單位數與上櫃權證，方向可能與金額相反 |
| `A12-screener-stats#4` | RC03 | P2→P2 | 部分成立 | 16:30 抓取的成交量值與 T86 投信數字不是定版，之後也不再更新 |
| `A12-screener-stats#5` | RC05 | P2→P2 | 部分成立 | 選股頁的 RSI14 與量比和 app 其他頁定義不同，也不符合台灣慣例 |
| `A12-screener-stats#6` | 單項 | P3→P3 | 確認成立 | 首頁「策略當日推薦 552 檔」重複加總各策略的命中數，實際不同股票只有 290 檔 |
| `A12-screener-stats#7` | RC13 | P3→P2 | 部分成立 | 選股降序排序把空值排在最前，5 日法人欄位永遠為空卻可以排序 |
| `A12-screener-stats#8` | RC22 | P3→P3 | 部分成立 | regime（市場環境）仍是 A 股校準，而且在此部署從未產出 |
| `A12-screener-stats#9` | RC15 | P3→P3 | 部分成立 | 市場快照的指數欄位寫死為 unavailable，另有分布與成交檔數的口徑細節 |
| `A12-screener-stats#10` | 單項 | P3→P3 | 被推翻 | 自選維度成員彈窗的平盤數把沒有行情的成員也算進去 |
| `A13-realtime#0` | RC02 | P1→P1 | 確認成立 | （跨領域）9/16、9/23 日K分區完全沒有上櫃資料，TPEx 抓取失敗卻回報 success，之後也不回補 |
| `A13-realtime#1` | RC11 | P2→P2 | 確認成立 | 收盤集合競價（13:30）結果從未進入即時路徑：今日K、指標、分鐘K在 13:30–16:30 停在集合競價前的價量 |
| `A13-realtime#2` | RC20 | P2→P2 | 確認成立 | 盤前、平日休市日與報價全部過期的空窗，每 6 秒下載一次無快取的 STOCK_DAY_ALL，結果全部丟棄 |
| `A13-realtime#3` | RC10 | P2→P2 | 部分成立 | 盤中量能口徑與歷史口徑不一致：量比與週轉率在盤中系統性偏低 4.8%–8.2% |
| `A13-realtime#4` | RC03 | P2→P2 | 確認成立 | （跨領域）同日寫入的日K成交量低於官方終值 |
| `A13-realtime#5` | 單項 | P3→P3 | 確認成立 | Yahoo 備援把當日最高價當成開盤價 |
| `A13-realtime#6` | RC18 | P3→P2 | 部分成立 | 分鐘K成交量歸屬錯位：Δ量整筆算給觀察到它的那一分鐘，空窗後的量集中在單一根 |
| `A13-realtime#7` | RC11 | P3→P3 | 確認成立 | 時段模型把 13:25–13:30 收盤集合競價當成連續交易 |
| `G01-gap#0` | RC01 | P1→P1 | 部分成立 | 除權息日以原始前收計算漲跌，產生假 PRICE_MOVE／PRICE_FLOW_DIVERGENCE／RELATIVE_STRENGTH_OUTLIER |
| `G01-gap#1` | RC02 | P2→P2 | 確認成立 | 上櫃缺日時診斷靜默少掉整個上櫃市場，data_quality 仍報 complete，面板與 AI 都看不出來 |
| `G01-gap#2` | RC17 | P2→P2 | 部分成立 | SHORT_SURGE 的「減少」在選股頁被標成「融券激增」 |
| `G01-gap#3` | RC26 | P2→P2 | 部分成立 | 融資／融券 20 日中位數為 0 時直接不評估，漏掉最極端的激增 |
| `G01-gap#4` | RC03 | P2→P2 | 確認成立 | 當日 16:30 寫入的日K量額與三大法人資料是初值，官方事後修正後不回補，使量能與法人訊號失真 |
| `G01-gap#5` | RC32 | P3→P3 | 部分成立 | 門檻比較前先四捨五入，使略低於門檻的值被判定觸發 |
| `G01-gap#6` | RC17 | P3→P2 | 部分成立 | 前端標籤與定義名稱不一致，「價量背離」誤用台灣慣用語；TURNOVER_SPIKE 命名易與週轉率混淆 |
| `G01-gap#7` | RC17 | P3→P3 | 確認成立 | 嚴重度分級與 evidence 欄位語意未文件化、刻度不一致，也沒有被任何畫面使用 |
| `G01-gap#8` | RC26 | P3→P3 | 確認成立 | 市場脈絡 advance_ratio 母體含 ETF，產業脈絡只含股票，兩者不可直接比較 |
| `G01-gap#9` | RC26 | P3→P3 | 部分成立 | RELATIVE_STRENGTH_OUTLIER 的產業等權報酬包含個股自身，小產業的離群值被稀釋 |
| `G01-gap#10` | RC33 | P3→P3 | 部分成立 | 法人 20 日基期以「有列的日數」為分母，與專案 B9「T86 缺列視為 0」的慣例不一致 |
| `G01-gap#11` | RC26 | P3→P3 | 確認成立 | SHORT_MARGIN_RATIO_SPIKE 沒有最低融資餘額門檻，極小基數會產生雜訊 |
| `G02-gap#0` | RC34 | P3→P3 | 部分成立 | 個股比較頁的本益比列，台股個股永遠顯示「N/A（不適用）」 |
| `G02-gap#1` | RC22 | P3→P3 | 確認成立 | 自選清單與資訊列的 10 個財務欄位（EPS、BPS、ROE、PE、PB、毛利率、淨利率、營收增速、淨利增速、負債率）在台股部署永遠沒有值 |
| `G02-gap#2` | RC17 | P3→P3 | 確認成立 | 財報卡的營業收入、營業利益、本期淨利是年初至今累計值，標籤卻沒有標示「累計」，也只顯示期末日 |
| `G02-gap#3` | RC27 | P3→P3 | 確認成立 | 股本卡把外幣面額當成新台幣顯示，小面額還會四捨五入成「0 元」；TDR 的股數是原股數 |
| `G02-gap#4` | RC20 | P3→P3 | 確認成立 | 財報的 6 個類別中任一個請求失敗（例外不是 LookupError），整張財報卡就顯示讀取失敗；log 中 TPEx 12 次有 3 次中止 |
| `G02-gap#5` | RC20 | P3→P3 | 部分成立 | 官方基本面沒有快取：每開一次個股頁就重新下載 10 份全市場資料，ETF 也要下載 2 次 |
| `G03-gap#0` | RC02 | P1→P1 | 確認成立 | 日線 store 9/16、9/23 缺整個上櫃市場，研究脈絡、比較頁與 AI 對上櫃標的與大盤統計靜默出錯，卻標為 complete |
| `G03-gap#1` | RC15 | P2→P2 | 確認成立 | 個股詳細頁「所屬大盤基準」卡永遠顯示 --（以空陣列解析指數） |
| `G03-gap#2` | RC11 | P2→P2 | 部分成立 | 目標日不在 store 時（每日 16:00～16:30，及平日國定假日），研究脈絡的價格欄全缺，previous_close 與漲跌停價退回前兩日的收盤 |
| `G03-gap#3` | RC03 | P2→P2 | 確認成立 | store 在當天 16:30 前後寫入的成交量／成交值，比官方最終值少（最多 −16.95%），且之後不會更新 |
| `G03-gap#4` | RC06 | P3→P3 | 確認成立 | market_rules_context.tick_size 沒有帶入 ETF 類別，0050、00878、00885、00893 都套用股票的升降單位 |
| `G03-gap#5` | RC17 | P3→P3 | 確認成立 | high_20d／low_20d 以收盤價計算卻沒有標註，AI 會把「距 20 日高點 0%」誤讀成股價位於最高點 |
| `G03-gap#6` | RC34 | P3→P3 | 確認成立 | 融資資料尚未公布時（16:30～21:40），AI 證據整段丟掉大盤與產業脈絡，但畫面仍顯示 |
| `G03-gap#7` | RC34 | P3→P3 | 確認成立 | 研究卡的證據計數與狀態標籤是寫死的；monitor_context 與 realtime_context 也標為 KNOWN 但不是實際資料 |
| `G03-gap#8` | RC15 | P3→P3 | 確認成立 | parse_taiex_rows 把第一列的昨收設為開盤價，月初第一天的漲跌錯誤 |
| `G03-gap#9` | RC34 | P3→P3 | 確認成立 | 比較頁把所有 null 一律顯示為「N/A（不適用）」，個股本益比與缺資料的收盤價都被說成「不適用」 |
| `G04-gap#0` | RC13 | P2→P2 | 部分成立 | 籌碼分布的換手率衰減在台股永遠不生效，實際輸出約 338 個交易日的無衰減累積成交量分布 |
| `G04-gap#1` | RC21 | P2→P2 | 確認成立 | 即時行情開啟時，樞軸點用當日未完成 K 棒計算，盤中持續漂移 |
| `G04-gap#2` | RC01 | P2→P2 | 部分成立 | 台股未還原價跨 0050 的 1 拆 4 分割，前高與籌碼分布混入分割前價格（2026-06 前 250 日新高也受影響） |
| `G04-gap#3` | RC21 | P2→P2 | 確認成立 | 缺口回補判定與自身 docstring 及通行定義不一致：部分回補即剔除、被跳空越過的缺口卻保留 |
| `G04-gap#4` | RC01 | P3→P3 | 確認成立 | 除權息造成的機械性跳空被當成「向下缺口」，並以缺口中點而不是填息價呈現 |
| `G04-gap#5` | RC21 | P3→P3 | 確認成立 | Keltner 三檔不是原作者定義：中軌用 SMA、ATR 固定 14 日，中期／長期參數為自訂 |
| `G04-gap#6` | RC30 | P3→P3 | 確認成立 | 布林帶使用樣本標準差（ddof=1），Bollinger 定義為母體標準差 |
| `G04-gap#7` | RC21 | P3→P3 | 部分成立 | 價位不依台股升降單位取整；整數關口步長沿用 A 股設計，00878 沒有任何整數關口 |
| `G04-gap#8` | RC21 | P3→P3 | 確認成立 | _aggregate_levels 註解說「保留最新」，實際保留價格最高者，且聚合會鏈式漂移超過 ±1% |
| `G04-gap#9` | RC21 | P3→P3 | 確認成立 | /levels 回傳的 series、dates、summary 沒有任何前端消費者，ATR series 仍命名為 stop_loss/take_profit |
| `G04-gap#10` | RC16 | P3→P3 | 部分成立 | 價位標籤為簡體中文，直接顯示在繁中 UI 與提醒名稱中 |
| `G05-gap#0` | RC01 | P1→P1 | 確認成立 | 除權息日：分時圖昨收線、百分比軸、線色、漲跌停線與日K資訊列漲跌幅都用前一日原始收盤價，沒有用參考價 |
| `G05-gap#1` | RC10 | P2→P2 | 確認成立 | 盤中『量比N』沒有依開盤經過時間折算，即時K棒的量比被系統性低估 |
| `G05-gap#2` | RC18 | P2→P2 | 確認成立 | 分時『均價』只涵蓋錄製有在跑的時段，和全日均價偏差最多 100bp，畫面沒有任何標示 |
| `G05-gap#3` | RC16 | P2→P2 | 確認成立 | 分時圖金額格式化把 10億 當成『1億』，10億以上的成交額顯示小 10 倍 |
| `G05-gap#4` | RC03 | P2→P2 | 確認成立 | K線量能輸入：日K成交量在 9/16～9/23 比官方 STOCK_DAY 少，VOL5 與量比跟著偏差（可能與日K資料領域重疊） |
| `G05-gap#5` | RC12 | P3→P2 | 確認成立 | 含國外成分的 ETF（00885、00893）沒有漲跌幅限制，分時圖仍畫 ±10% 漲跌停線 |
| `G05-gap#6` | RC06 | P3→P3 | 確認成立 | 分時漲跌停線沒有依升降單位取整（沿用 A 股『四捨五入到分』） |
| `G05-gap#7` | RC16 | P3→P3 | 確認成立 | 成交量以『股』搭配萬／億顯示，和台股以『張』為單位的慣例不一致；另有『10000萬』的邊界 |
| `G05-gap#8` | RC18 | P3→P3 | 確認成立 | 迷你分時的均線用收盤價簡單平均、橫軸按資料點等距，和個股分時圖的 VWAP 與時間軸不一致 |
| `G05-gap#9` | RC18 | P3→P3 | 確認成立 | 分時圖 connectNulls: true 把沒錄到的時段連成直線，和『缺口代表沒在看』的設計相反 |
| `G05-gap#10` | RC29 | P3→P3 | 確認成立 | 日K資訊列換手率公式沿用 A 股『手』的換算，多乘 100（目前台股流通股數為空，尚未觸發） |
| `G06-gap#0` | RC27 | P2→P2 | 確認成立 | 代碼 91（臺灣存託憑證）被當成一般股票，形成『未知產業（91）』偽產業、被算進已分類，還觸發錯誤的相對強弱訊號 |
| `G06-gap#1` | RC14 | P3→P3 | 確認成立 | 11 檔上市 ETF 的 etf_category 與官方基金類型矛盾：主動式債券與期貨信託 ETF 被標成 foreign_equity，00716R 反向型也在其中 |
| `G06-gap#2` | RC14 | P3→P3 | 確認成立 | Security Master 的 listing_date 混用民國 7 碼、YYYYMMDD、YYYY/MM/DD 三種格式，違反宣告的日期契約 |
