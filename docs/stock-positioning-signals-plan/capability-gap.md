# 能力差距分析

> 評估基準：HEAD `3c4afc7`，評估日 2026-10-27。行號連結都對這個版本。
> 對照基準是 [finlab-reference.md](finlab-reference.md) 的 14 區塊、9 指標與 3 個訊號，區塊編號相同。結論與分期以 [README.md](README.md) 為準。
> 本頁提到的新端點、元件、store、模組都是提案，尚不存在。

## 判定與規模

| 判定 | 意思 |
|---|---|
| 可做 | 用現有資料與口徑即可，不違反 6G |
| 換口徑 | 可做，但本專案的資料口徑與 finlab 不同，畫面須標明 |
| 需決策 | 卡在 6G 或範圍，要先決定 PD1、PD2 或 PD3 |
| 需新資料源 | 本專案沒有這份資料，要接新的官方來源或回補 |
| 不建議 | 資料或使用條件不允許，或價值低 |
| 未查明 | 證據不足，無法判定 |

規模只表示改動範圍，不代表工時：小＝單一模組內、用既有資料、不新增端點；中＝新服務模組或新端點，加前端區塊，用既有資料；大＝新資料源、回補流程或新 store。

## 1. 本專案現有基礎

| 能力 | 現況（事實） | 證據 |
|---|---|---|
| 官方基本面目前參考 | 月營收、財報、估值、股本每次請求抓官方全市場清單，只在記憶體依（url、台北今日）快取，換日清掉；沒有任何程式把估值、月營收、財報寫成歷史檔 | [fundamentals.py:156](../../backend/app/taiwan/fundamentals.py#L156)、[:289-308](../../backend/app/taiwan/fundamentals.py#L289-L308) |
| 可用時間 | 全部 `available_at=None`、`status=data_insufficient`；能力矩陣標 `current_reference` | [fundamentals.py:268-271](../../backend/app/taiwan/fundamentals.py#L268-L271)、[current_data.py:31-75](../../backend/app/taiwan/current_data.py#L31-L75) |
| 估值 | 只有當日快照：上市 `exchangeReport/BWIBBU_ALL`、上櫃 `tpex_mainboard_peratio_analysis`；ETF 回資料不足 | [fundamentals.py:211-223](../../backend/app/taiwan/fundamentals.py#L211-L223) |
| 財報 | 只取營收、營業利益、淨利、累計 EPS、資產、負債、權益，沒有毛利；損益是年初至今累計 | [fundamentals.py:199-209](../../backend/app/taiwan/fundamentals.py#L199-L209) |
| 月營收 | 只有最新一個月，含月增、年增（百分數）與當年累計 | [fundamentals.py:159-173](../../backend/app/taiwan/fundamentals.py#L159-L173) |
| 法人、融資融券 | 逐日分區；回補預設 60、上限 250 個交易日 | [institutional_store.py:7](../../backend/app/taiwan/institutional_store.py#L7)、[margin_store.py:7](../../backend/app/taiwan/margin_store.py#L7)、[enrichment_backfill.py:34-35](../../backend/app/taiwan/enrichment_backfill.py#L34-L35) |
| 日 K 深度 | 由啟動資料包決定（2024-01-02～2026-09-10），之後增量 | [bootstrap.py:49-54](../../backend/app/taiwan/bootstrap.py#L49-L54) |
| 指數 | 價格指數（加權、櫃買）按月回補；沒有存報酬指數 | [index_daily.py:24-25](../../backend/app/taiwan/index_daily.py#L24-L25)、[snapshot_provider.py:186-193](../../backend/app/taiwan/providers/snapshot_provider.py#L186-L193) |
| 公司行動 | 除權息等事件自 2024 起，有參考價與除權息因子，沒有每股股利欄位 | [corporate_actions.py:21](../../backend/app/taiwan/corporate_actions.py#L21)、[:88-106](../../backend/app/taiwan/corporate_actions.py#L88-L106) |
| 股利事件 | MOPS 事件有程式、有每股現金與股票股利欄位，能力矩陣標 `pit_historical`，但沒有正式呼叫者 | [dividend_events.py:40-51](../../backend/app/taiwan/dividend_events.py#L40-L51)、[current_data.py:76-86](../../backend/app/taiwan/current_data.py#L76-L86)、[current_data.py:176-181](../../backend/app/taiwan/current_data.py#L176-L181) |
| 訊號 | 內建 `signal_*` 16 個、漲跌停 4 個、`vp_*` 6 個、自訂 `csg_*`，隨 enriched 盤後全市場逐日發佈 | [pipeline.py:168-187](../../backend/app/indicators/pipeline.py#L168-L187)、[volume_patterns.py:13-20](../../backend/app/taiwan/volume_patterns.py#L13-L20)、[custom_signals.py:10-14](../../backend/app/strategy/custom_signals.py#L10-L14) |
| 策略 | 21 支內建策略；盤後快取只存最新一天，另逐日記錄選股快照 | [strategy/builtin/](../../backend/app/strategy/builtin/)、[strategy_cache.py:38](../../backend/app/services/strategy_cache.py#L38)、[daily_pipeline.py:89](../../backend/app/jobs/daily_pipeline.py#L89) |
| 單股訊號查詢 | 沒有「給一檔股票回傳目前觸發訊號或入選策略」的 API。`GET /api/kline/daily` 回傳單股逐日 enriched 列（含 `signal_*`）；個股詳情帶近 7 日最多 10 筆警示；自選頁帶最新一日的 13 個 `signal_*` | [kline.py:495-503](../../backend/app/api/kline.py#L495-L503)、[detail_service.py:1006-1020](../../backend/app/taiwan/detail_service.py#L1006-L1020)、[watchlist.py:260](../../backend/app/api/watchlist.py#L260)、[watchlist_enrichment.py:54-61](../../backend/app/taiwan/watchlist_enrichment.py#L54-L61) |
| 歷史統計 | 沒有訊號層級的統計；只有策略選股績效（依策略彙總） | [strategy_pick_history.py:491](../../backend/app/services/strategy_pick_history.py#L491)、[strategy_pick_performance.py:370](../../backend/app/services/strategy_pick_performance.py#L370)、[:539-561](../../backend/app/services/strategy_pick_performance.py#L539-L561)、[screener.py:468](../../backend/app/api/screener.py#L468) |
| 產業 | 只有一層；對照表 34 個代碼（代碼 91 是 TDR 標記，不在表內）；產業彙總只有價量與籌碼 | [industry_classification.py:35-70](../../backend/app/taiwan/universe/industry_classification.py#L35-L70)、代碼 91 見 [:23-27](../../backend/app/taiwan/universe/industry_classification.py#L23-L27)、[models.py:137](../../backend/app/taiwan/universe/models.py#L137)、[industry_intelligence.py:1-17](../../backend/app/taiwan/industry_intelligence.py#L1-L17) |
| 前端擴展 | 路由頁 `TaiwanStockDetail.tsx` 沒有插槽；預覽對話框有 `stock-preview.footer`，context 只有代號、名稱、檢視模式 | [StockPreviewDialog.tsx:484-487](../../frontend/src/components/StockPreviewDialog.tsx#L484-L487)、[extensions/types.ts:6-26](../../frontend/src/extensions/types.ts#L6-L26) |

補充：對照表的說明文字同時寫「34 codes」與「33-industry classification scheme」（[industry_classification.py:19-21](../../backend/app/taiwan/universe/industry_classification.py#L19-L21)），表內實數是 34 個代碼。

## 2. 14 區塊對照

| # | finlab 區塊 | 本專案現況 | 判定 | 所需改動 | 規模 |
|---|---|---|---|---|---|
| 1 | 報價與 K 線 | 個股詳情已有報價、開高低、量、漲跌幅規則（[taiwan.py:262](../../backend/app/api/taiwan.py#L262)）；目前參考有本益比、淨值比、殖利率。路由頁 K 線只開量子圖（[TaiwanStockDetail.tsx:50](../../frontend/src/pages/TaiwanStockDetail.tsx#L50)），資料列不帶均線欄位（[:124-135](../../frontend/src/pages/TaiwanStockDetail.tsx#L124-L135)），而圖表要有均線欄位才畫（[EChartsCandlestick.tsx:593](../../frontend/src/components/EChartsCandlestick.tsx#L593)）；推論路由頁不畫均線與指標（未在瀏覽器確認）。預覽對話框的日 K 有 MACD、RSI、KDJ、布林（[StockDailyKChart.tsx:76-86](../../frontend/src/components/StockDailyKChart.tsx#L76-L86)）。缺近四季 EPS（只有年初至今累計 EPS，[fundamentals.py:203](../../backend/app/taiwan/fundamentals.py#L203)）與外資持股比率（程式中沒有來源） | 大多可做；近四季 EPS 需決策；外資持股比率需新資料源（官方端點未查證） | 路由頁改用預覽對話框的日 K 圖；報價補 52 週高低（日 K 已有一年以上） | 中 |
| 2 | 體質與估值定位 | 見第 3 節 | 可做（ROE、營業利益率換口徑） | 新服務模組算全市場百分位與分級；前端新卡 | 中 |
| 3 | 今日重點 | 訊號標籤：1-1 完成後可做；營收：目前月營收可做；法人近 5 日：個股詳情已有 5 日累計（[detail_models.py:107-109](../../backend/app/taiwan/detail_models.py#L107-L109)）；除息：股利事件程式可查 MOPS 公告，但沒有正式呼叫者；本益比自身歷史位置：需要估值歷史 | 部分可做；估值位置需決策 | 重用 1-1、1-2 的結果組句；除息需接線股利事件 | 小～中 |
| 4 | 合理價與歷史估值 | 沒有估值歷史 | 需決策＋需新資料源；同業模型未查明 | 估值回補與僅供顯示的 store（第二期） | 大 |
| 5 | EPS 與獲利能力 | 只有最新一季的年初至今累計；沒有毛利。官方 `t187ap06_L_ci` 與 `mopsfin_t187ap06_O_ci` 有毛利欄位，但只有最新一季（[data-sources.md](data-sources.md) 第 2 節） | 毛利解析可做；近 8 季需決策（從現在起每季存快照）；要回補過去各季需新資料源（MOPS 歷史查詢，未查明） | 毛利解析（第一期 1-4）；每季快照（第二期）。單季值 = 本季累計 − 上季累計，第 4 季 = 全年 − 第 3 季累計，所以至少要連續兩季快照 | 小（毛利）／大（8 季） |
| 6 | 月營收 12 個月 | 只有最新一個月 | 需決策＋需新資料源（MOPS `t21sc03`） | 回補與僅供顯示的 store（第二期） | 大 |
| 7 | 股利 | 殖利率目前值有（[fundamentals.py:221](../../backend/app/taiwan/fundamentals.py#L221)）；除息事件自 2024 起，沒有每股股利；股利事件程式有每股股利欄位但沒接線 | 部分可做 | 接線股利事件（已是 `pit_historical`，不違反 6G）；近 12 個月現金股利由事件加總 | 中 |
| 8 | 法人與融資券 | 已有：個股詳情、5 日累計、歷史彈窗 5–250 日（[taiwan.py:293](../../backend/app/api/taiwan.py#L293)、[:318](../../backend/app/api/taiwan.py#L318)）。融資 store 沒有融資限額欄位（[margin_store.py:27-48](../../backend/app/taiwan/margin_store.py#L27-L48)） | 已有；融資使用率未查明（官方回應是否含限額未查） | 若官方有限額欄位，在既有 adapter 加解析 | 小 |
| 9 | 同業比較 | 產業一層；沒有基本面同業排名 | 產業版可做；概念股不建議（沒有官方資料） | 1-3，與 1-2 共用計算 | 中 |
| 10 | 預測遊戲 | 沒有 | 不建議 | — | — |
| 11 | 主力分點與大戶籌碼 | 沒有分點、集保資料 | 分點不建議（不可行）；集保需新資料源，只能從現在起每週累積 | 第三期：TDCC 週資料 | 大 |
| 12 | 量化訊號回測 | 沒有訊號統計；策略選股績效只能依策略查 | 部分可做：技術面訊號統計可做；營收與估值類需決策，且 PD1 下仍不開放 | 1-6 | 中 |
| 13 | AI、簡介、上下游、常見問題 | AI 研究已有（[taiwan.py:372](../../backend/app/api/taiwan.py#L372)）；簡介與上下游沒有資料源 | AI 已有；簡介沒有資料源（是否有可用的官方來源未查明）；上下游不建議（沒有官方資料）；常見問題模板可做 | 常見問題以 1-1、1-2 的結果套模板 | 小 |
| 14 | 盤後 Email 通知 | 通知管道有系統通知與 webhook（Telegram 等）；`backend/app` 搜尋 `smtp`、`email` 0 筆 | 未查明（需求與寄送方式未定） | — | — |

## 3. 體質與估值定位的 9 個指標

### 3.1 逐項判定

| 指標 | finlab 口徑 | 本專案資料 | 判定 | 本專案口徑（提案） | 注意 |
|---|---|---|---|---|---|
| 本益比 | 當日、反向 | 估值 `pe`（[fundamentals.py:219](../../backend/app/taiwan/fundamentals.py#L219)） | 可做 | 同 finlab | ETF 不提供（[:212-213](../../backend/app/taiwan/fundamentals.py#L212-L213)）；沒有數值者排除，不排名 |
| 股價淨值比 | 當日、反向 | `pb`（[:220](../../backend/app/taiwan/fundamentals.py#L220)） | 可做 | 同 finlab | 同上 |
| 殖利率 | 當日 | `dividend_yield`（[:221](../../backend/app/taiwan/fundamentals.py#L221)） | 可做 | 同 finlab | ETF 不提供 |
| ROE | 最新一季、單季、稅後 | 年初至今累計淨利、期末權益總計（[:202](../../backend/app/taiwan/fundamentals.py#L202)、[:206](../../backend/app/taiwan/fundamentals.py#L206)） | 換口徑 | 累計年化 ROE = 累計淨利 × 4 ÷ 季別 ÷ 期末權益總計，標「年初至今累計年化」與期別 | 推論：淨利與權益都含非控制權益；沒有前期權益，不能用平均權益；數值與 finlab 不可直接比 |
| 營業利益率 | 最新一季 | 累計營業利益 ÷ 累計營收（[:200-201](../../backend/app/taiwan/fundamentals.py#L200-L201)） | 換口徑 | 年初至今累計營業利益率 | 金融保險類可能沒有營業利益（測試樣本 2881 只有淨收益，[test_taiwan_fundamentals.py:39](../../backend/tests/test_taiwan_fundamentals.py#L39)）；建議只排一般業（`accounting_category` 為 `ci`，[fundamentals.py:209](../../backend/app/taiwan/fundamentals.py#L209)） |
| 月營收年增率 | 最新公告月份單月 | `yoy`，百分數（[:169](../../backend/app/taiwan/fundamentals.py#L169)） | 可做 | 同 finlab | 每月 1–10 日全市場清單可能混雜不同月份（未查明），只與同一資料年月比較或標示 |
| 60 日報酬 | 還原股價 60 日 | enriched `momentum_60d`，前復權收盤、小數（[pipeline.py:637-638](../../backend/app/indicators/pipeline.py#L637-L638)） | 可做 | 同 finlab | 小數轉百分數在邊界做 |
| 240 日報酬 | 還原股價 240 日 | 日 K 自 2024-01-02 | 可做 | 以日 K 與前復權計算；單一回看收盤可用 `factors_between`（[secondary-development.md:42-47](../secondary-development.md#L42-L47)） | 上市未滿 240 個交易日的排除 |
| 外資 20 日買超強度 | 外資 20 日買賣超 ÷ 同期成交量 | 法人 `foreign_net`（股，[institutional_store.py:33-58](../../backend/app/taiwan/institutional_store.py#L33-L58)）、日 K `volume`（股，[snapshot_provider.py:11](../../backend/app/taiwan/providers/snapshot_provider.py#L11)） | 可做 | 同 finlab | 本專案的外資是外陸資、不含外資自營商（封存稽核的欄位比對：[A07-chips.md:517](../audit-2026-09-24/sections/A07-chips.md#L517)、[:520](../audit-2026-09-24/sections/A07-chips.md#L520)）；20 日以交易日曆計，缺分區的股票不排名 |

### 3.2 共通設計注意

1. 6G 相容的條件：只算當日橫斷面、結果只放記憶體快取、不寫入歷史檔，也不供選股歷史或回測使用（[taiwan-data-sources.md:768-769](../taiwan-data-sources.md#L768-L769)）。
2. 母體：建議上市加上櫃普通股；ETF、TDR 另列不排名。finlab 的母體未查明，數值不必對齊。
3. 期別：財報申報期與每月 1–10 日，全市場清單可能混雜不同期別（未查明）；排名限同一期別，或在畫面標示期別。
4. 快取：每個資料日期算一次，鍵含資料集與資料日期；計算在鎖外進行，完成後原子替換。
5. 失敗隔離：任一資料集失敗只讓對應指標顯示「無法計算」。目前 B201 會讓整張財報卡失敗（[fundamentals.py:181-190](../../backend/app/taiwan/fundamentals.py#L181-L190)），要一起處理。
6. 存取：全市場清單目前只能從私有的 `_rows` 取得（[fundamentals.py:289](../../backend/app/taiwan/fundamentals.py#L289)），需要新增公開方法（提案）。
7. 分級門檻可沿用 finlab 的級距（[finlab-reference.md](finlab-reference.md) 3.3）或自訂，開工前定。

### 3.3 官方請求數估算

推論，依程式結構計算：財報每個類別各有損益與資產負債兩份清單、共 6 類（[fundamentals.py:23](../../backend/app/taiwan/fundamentals.py#L23)、[:178-190](../../backend/app/taiwan/fundamentals.py#L178-L190)），兩個市場合計 24 份；月營收與估值各 2 份。當日第一次計算最多 28 份清單，之後同一台北日期讀快取（[fundamentals.py:146-148](../../backend/app/taiwan/fundamentals.py#L146-L148)）。60／240 日報酬與外資強度只讀已存的日 K、法人分區，不發請求。

## 4. 訊號

### 4.1 「這檔目前觸發的訊號」的來源

| 來源 | 數量 | 位置 | 單股讀法 | 判定 |
|---|---|---|---|---|
| 內建技術訊號 `signal_*` | 16 | [pipeline.py:168-183](../../backend/app/indicators/pipeline.py#L168-L183) | 讀最新已發佈 enriched 分區的該檔一列（可沿用 [enriched_reader.py:69](../../backend/app/taiwan/enriched_reader.py#L69) 的 `read_latest_enriched`） | 可做 |
| 漲跌停訊號 | 4 | [pipeline.py:184-187](../../backend/app/indicators/pipeline.py#L184-L187) | 同上 | 可做 |
| 量價型態 `vp_*` | 6 | [volume_patterns.py:13-20](../../backend/app/taiwan/volume_patterns.py#L13-L20) | 同上；只在台股 enriched 發佈時加入，刻意不放進 `ENRICHED_COLUMNS`（[:1-5](../../backend/app/taiwan/volume_patterns.py#L1-L5)） | 可做 |
| 自訂訊號 `csg_*` | 使用者定義 | [custom_signals.py:10-14](../../backend/app/strategy/custom_signals.py#L10-L14) | 同上；新增訊號後舊分區是否回填未查明 | 可做 |
| 策略入選 | 21 支內建加使用者策略 | [screener.py:301](../../backend/app/api/screener.py#L301)；回應依策略分組列出選股（[:325-331](../../backend/app/api/screener.py#L325-L331)） | 沿用 `/cached` 的 `_cached_with_realtime`（[screener.py:211-214](../../backend/app/api/screener.py#L211-L214)：先讀 [strategy_cache.py:64](../../backend/app/services/strategy_cache.py#L64) 的 `read_cache`，再疊上監控引擎較新交易日的結果），依代號過濾；只讀 `read_cache` 會在盤中與 `/cached` 的資料日期不同 | 可做 |
| 近期警示 | 近 7 日最多 10 筆 | [detail_service.py:1006-1020](../../backend/app/taiwan/detail_service.py#L1006-L1020) | 個股詳情已有 | 已有 |
| 異常診斷 | 10 類 | 每次請求做全市場計算，沒有單股入口（[稽核 README:266](../audit-2026-10-22/README.md#L266)） | — | 第一期不納入 |

中文名稱：前端已有 `BUILTIN_SIGNAL_DEFINITIONS`（[signals.ts:21](../../frontend/src/lib/signals.ts#L21)）、`cnSignal`（[:227](../../frontend/src/lib/signals.ts#L227)）、`VOLUME_PATTERN_DEFINITIONS`（[:401](../../frontend/src/lib/signals.ts#L401)）。自選頁的 `SIGNAL_FIELDS` 只有 13 個，缺跌停與 MA5／MA10（[stock-table.ts:20-34](../../frontend/src/lib/stock-table.ts#L20-L34)），新區塊不要沿用它。

提案：在 `backend/app/taiwan/` 加一個服務模組組合上表各來源，`backend/app/api/taiwan.py` 加薄端點（例如 `GET /api/taiwan/stocks/{symbol}/signals`，提案，尚不存在）。前端直接打 `/api/screener/cached` 再過濾也可行，但每次會拿到全部策略的全部選股，列表頁效能要求（[CONTRIBUTING.md:192](../../CONTRIBUTING.md#L192)）下不建議。

### 4.2 歷史勝率

- 事實：沒有訊號層級的統計程式。最接近的是策略選股績效：口徑為次日開盤進場、前復權含息、不扣成本與滑價（[strategy_pick_performance.py:563-571](../../backend/app/services/strategy_pick_performance.py#L563-L571)），期間 1、5、10、20 日（[:33](../../backend/app/services/strategy_pick_performance.py#L33)），每期有樣本數、平均、中位數、上漲比例（[:485-499](../../backend/app/services/strategy_pick_performance.py#L485-L499)），依策略彙總（[:557-561](../../backend/app/services/strategy_pick_performance.py#L557-L561)）。
- 提案：新寫訊號事件統計，口徑沿用上述；20、60、120 日期間須新增。第一期只做 20、60 日。
- 樣本限制：日 K 從 2024-01-02 起，單檔樣本少，須附樣本數；也可另做全市場合併統計（同一訊號全市場所有事件）。
- 事件定義：`signal_*` 是逐日狀態，要定義首次觸發與冷卻期，避免連續多日重複計數。

### 4.3 finlab 的 3 個訊號

| 訊號 | 觸發判定 | 歷史統計判定 | 理由 |
|---|---|---|---|
| 252 日新高 | 可做 | 可做，但樣本受限：須附樣本數，或改成全市場合併統計（換口徑） | 現有 `signal_n_day_high` 是收盤達 60 日最高（[pipeline.py:179](../../backend/app/indicators/pipeline.py#L179)），要新增 252 日版本。日 K 自 2024-01 起，要先有 252 個交易日回看，事件最早約在 2025 年初（推論），樣本少 |
| 外資連 5 買 | 可做 | 可做，但樣本受限：20、60 日樣本少，120 日幾乎沒有樣本 | 法人歷史上限 250 個交易日（[enrichment_backfill.py:34-35](../../backend/app/taiwan/enrichment_backfill.py#L34-L35)）；120 日期間只剩最早約 130 個交易日內的事件可用（推論） |
| 月營收年增 ≥ 20% | 可做（用目前月營收年增率） | 不可（需決策，PD1 下仍不開放） | 沒有營收歷史；finlab 的事件日用公告截止日後第一個交易日，是 6G 明列拒絕的對應（[6G:72-80](../taiwan-fundamentals-availability-phase-6g.md#L72-L80)） |

### 4.4 基準指數

- 事實：沒有存發行量加權股價報酬指數，`backend/app` 搜尋「報酬指數」0 筆；價格指數來自 [index_daily.py:24-25](../../backend/app/taiwan/index_daily.py#L24-L25)，標的定義在 [benchmarks.py:19-20](../../backend/app/taiwan/benchmarks.py#L19-L20)。
- 事實：每日抓的 `MI_INDEX?type=ALL`（[snapshot_provider.py:175](../../backend/app/taiwan/providers/snapshot_provider.py#L175)）只取收盤行情表，其餘丟棄（[:186-193](../../backend/app/taiwan/providers/snapshot_provider.py#L186-L193)）；外部查證確認回應含報酬指數表（[data-sources.md](data-sources.md) 第 2 節）。
- 判定：落地是小改動（第一期 1-5），從上線日起累積。歷史超額報酬要等覆蓋足夠，或回補過去日期（PD2）。不得用不含息的價格指數與前復權含息報酬混算。
- 上櫃股：櫃買報酬指數是否可得未查明；目前上櫃股的大盤基準多半顯示 unavailable（[稽核 README:263](../audit-2026-10-22/README.md#L263)）。

## 5. 前端接入

- 路由頁沒有插槽（`frontend/src` 內的 `<ExtensionSlot` 只在 `Layout.tsx`、`StockPreviewDialog.tsx`、`Watchlist.tsx`）；預覽對話框內嵌同一個 `TaiwanStockDetail`（[StockPreviewDialog.tsx:454-460](../../frontend/src/components/StockPreviewDialog.tsx#L454-L460)）。
- 甲案直接改核心頁、乙案先加插槽，兩案並陳於 [README.md](README.md) 的「共通約束」。`stock-preview.footer` 只在對話框出現、context 只有代號、名稱與檢視模式，不夠放新區塊。
- 新元件可放 `frontend/src/components/taiwan/`，同類先例是 `TaiwanReferenceData.tsx`；它已在畫面標示「目前參考」與不得用於歷史回測（[TaiwanReferenceData.tsx:236-239](../../frontend/src/components/taiwan/TaiwanReferenceData.tsx#L236-L239)），體質卡比照。
- 高衝突熱點 `api.ts`、`queryKeys.ts` 一定會改（[secondary-development.md:291-299](../secondary-development.md#L291-L299)），改動保持最小。

## 6. 推論與未查明

推論：

1. 路由頁 K 線不畫均線與指標（依資料欄位推得，未在瀏覽器確認）。
2. `vp_*` 與 `csg_*` 會出現在 `/api/kline/daily` 的 enriched 回應中（未實跑）；本地補算路徑重算 enriched（[kline.py:527-533](../../backend/app/api/kline.py#L527-L533)），推論那時不含 `vp_*`。
3. 252 日新高的歷史事件最早約在 2025 年初；外資連 5 買的 120 日樣本幾乎沒有。
4. ROE 計算用的淨利與權益都含非控制權益。

未查明：

1. 財報申報期與每月 1–10 日的全市場清單是否混雜不同期別。
2. 融資限額是否在官方融資融券回應中（融資使用率）。
3. 外資持股比率的官方端點。
4. 櫃買報酬指數是否可得；歷史日期的 MI_INDEX 是否都有報酬指數表。
5. finlab 同業模型應得價的算法。
6. 新增 `csg_*` 後舊分區是否回填。
7. Email 通知的需求與寄送方式。
