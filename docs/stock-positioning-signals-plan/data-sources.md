# 官方歷史資料來源查證

> 評估基準：HEAD `3c4afc7`。外部查證日 2026-10-27（台北時間；上櫃券商分點一列為 2026-10-28），是實際連線官方網站的結果；官方網站改版後可能不同，實作前請重新確認。
> 本頁是評估當時的查證紀錄。實作後，資料源事實的正本移到 `docs/taiwan-data-sources.md`。
> 標示：「事實」附行號連結或 URL；「推論」由事實推得；「未查明」是查不到或沒有查。

## 1. 本專案現有資料的歷史深度

| 資料 | 儲存方式 | 歷史深度 | 證據 |
|---|---|---|---|
| 日 K | 日 K store，逐日分區 | 由啟動資料包決定：2024-01-02～2026-09-10，之後每日增量 | [bootstrap.py:49-54](../../backend/app/taiwan/bootstrap.py#L49-L54) |
| 三大法人（個股） | `taiwan/institutional/date=YYYY-MM-DD/part.parquet`，單位股 | 回補預設 60、上限 250 個交易日 | [institutional_store.py:7](../../backend/app/taiwan/institutional_store.py#L7)、[enrichment_backfill.py:34-35](../../backend/app/taiwan/enrichment_backfill.py#L34-L35) |
| 融資融券 | `taiwan/margin/date=YYYY-MM-DD/part.parquet` | 同上 | [margin_store.py:7](../../backend/app/taiwan/margin_store.py#L7) |
| 價格指數 | 加權、櫃買按月回補 | 每日管線只回補加權指數（[稽核 README:263](../audit-2026-10-22/README.md#L263)） | [index_daily.py:24-25](../../backend/app/taiwan/index_daily.py#L24-L25) |
| 報酬指數 | 沒有存 | — | [snapshot_provider.py:186-193](../../backend/app/taiwan/providers/snapshot_provider.py#L186-L193) |
| 公司行動 | `events.parquet` | 回補指令從 2024-01-01 起 | [corporate_actions.py:21](../../backend/app/taiwan/corporate_actions.py#L21) |
| 估值、月營收、財報 | 沒有 store；記憶體 dict 以（url、台北今日）為鍵，換日清掉 | 只有當日 | [fundamentals.py:156](../../backend/app/taiwan/fundamentals.py#L156)、[:291](../../backend/app/taiwan/fundamentals.py#L291)、[:305-307](../../backend/app/taiwan/fundamentals.py#L305-L307) |
| 股本 | `share_capital.parquet`，每次整檔取代 | 只有目前值 | [share_capital_store.py:178](../../backend/app/taiwan/share_capital_store.py#L178)、[:232](../../backend/app/taiwan/share_capital_store.py#L232) |
| 股利事件 | 沒有 store，查詢時打 MOPS | 來源可依期間查詢（推論） | [dividend_events.py:23-24](../../backend/app/taiwan/dividend_events.py#L23-L24) |

## 2. 已查證的官方端點

以下 URL 與結果除另行註明外，都是 2026-10-27 的查證。

| 資料 | 端點 | 最早日期 | 限制 | 本專案現況 |
|---|---|---|---|---|
| 上市個股本益比、殖利率、淨值比（日） | `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=YYYYMMDD&selectType=ALL&response=json` | 2005-09-02（查 20050901 時回覆「查詢日期小於94年9月2日」） | 一次一個交易日、全市場 | 只用 OpenAPI `exchangeReport/BWIBBU_ALL` 的當日值（[fundamentals.py:214](../../backend/app/taiwan/fundamentals.py#L214)） |
| 上櫃個股本益比（日） | `https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date=YYYY/MM/DD&response=json` | 推定 2007-01-02（抽樣推定，不是官方說明） | 一次一個交易日 | 只用 OpenAPI `tpex_mainboard_peratio_analysis` 的當日值 |
| 月營收彙總表 | `https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{民國年}_{月}_0.html`（上市）；把 `sii` 換成 `otc` 為上櫃 | 民國 113 年 1 月與 102 年 1 月實測 HTTP 200；更早未測 | HTML 表格要解析；欄位是否與 OpenAPI 月營收相同未查明 | 只用 OpenAPI `t187ap05_L`、`mopsfin_t187ap05_O`，只有單月（[fundamentals.py:162](../../backend/app/taiwan/fundamentals.py#L162)） |
| 發行量加權股價報酬指數 | `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=YYYYMMDD&type=ALL&response=json` 回應中的報酬指數表 | 歷史日期是否都有這張表未查明 | 每日已經在抓 | 只取收盤行情表，其餘丟棄（[snapshot_provider.py:175](../../backend/app/taiwan/providers/snapshot_provider.py#L175)、[:186-193](../../backend/app/taiwan/providers/snapshot_provider.py#L186-L193)） |
| 財報毛利 | `https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci`、`https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci` | 只有最新一季（查證時為民國 115 年第 2 季） | 有「營業毛利（毛損）」「營業毛利（毛損）淨額」欄位；沒有歷史季別 | 解析的欄位不含毛利（[fundamentals.py:199-207](../../backend/app/taiwan/fundamentals.py#L199-L207)） |
| 集保股權分散（開放資料） | `https://opendata.tdcc.com.tw/getOD.ashx?id=1-5` | 只有最新一週 | 不能回補 | 沒有 |
| 集保股權分散（逐檔查詢） | `https://www.tdcc.com.tw/portal/zh/smWeb/qryStock` | 約 51 週 | 一次查一檔；查證時未見驗證碼 | 沒有 |
| 上市券商分點 | `https://bsr.twse.com.tw/bshtm/bsMenu.aspx` | — | 需圖形驗證碼，頁面註明「此資料不得逕自散布或販售」 | 沒有；不建議 |
| 上櫃券商分點 | `https://www.tpex.org.tw/zh-tw/mainboard/trading/info/brokerBS.html`（2026-10-28 查證） | 只提供當日 | 頁面要求驗證非機器人（Turnstile） | 沒有；不建議 |

月營收 OpenAPI（`t187ap05_L`、`mopsfin_t187ap05_O`）只有單月，這也是查證結果。

## 3. 本專案的取用方式

- 官方資料一律經 `backend/app/taiwan/` 既有的 adapter → normalizer／model → consumer 路徑接入，不另建平行 provider（[CONTRIBUTING.md:136](../../CONTRIBUTING.md#L136)、[secondary-development.md:25-26](../secondary-development.md#L25-L26)）。
- 新增資料源要同步 `docs/custom-data-source.md` 或 `docs/plugin-development.md` 的契約（[CONTRIBUTING.md:139](../../CONTRIBUTING.md#L139)），能力矩陣 `CAPABILITIES`（[current_data.py:31](../../backend/app/taiwan/current_data.py#L31)）與 `docs/taiwan-data-sources.md` 一起更新。
- 交易所會節流：既有回補把 307、308、428、429 視為節流（[institutional_margin_refresh.py:44](../../backend/app/taiwan/institutional_margin_refresh.py#L44)），請求間隔 0.4 秒（[:48](../../backend/app/taiwan/institutional_margin_refresh.py#L48)），並統計被拒絕的天數（[enrichment_backfill.py:39](../../backend/app/taiwan/enrichment_backfill.py#L39)）。新回補比照，間隔要實測後再定。

## 4. 與 6G 的衝突

### 4.1 6G 的規則

- 財報、月營收、估值三個資料集都維持 `published_at=None`、`available_at=None`、`status=data_insufficient`，這是刻意的 fail-closed（[6G:15-17](../taiwan-fundamentals-availability-phase-6g.md#L15-L17)）。
- 理由：彙總列沒有可對應的文件識別或首次公開時間（[6G:21-27](../taiwan-fundamentals-availability-phase-6g.md#L21-L27)）。
- 不能拿來當可用時間的依據，明列包括：出表日期、期末日、交易日、抓取時間、HTTP header、法定申報期限、收盤、當日結束、下一個交易日（[6G:72-80](../taiwan-fundamentals-availability-phase-6g.md#L72-L80)）。
- 可見條件是 `query_at > available_at`；有值但沒有 `available_at` 的資料不得退回用目前值（[6G:93-96](../taiwan-fundamentals-availability-phase-6g.md#L93-L96)）。
- 目前參考的區塊不得進入已驗證因子、歷史選股、換手率歷史與回測（[taiwan-data-sources.md:768-769](../taiwan-data-sources.md#L768-L769)）。

### 4.2 逐項對照

| 功能 | 需要的歷史 | 與 6G 的關係 | 結論 |
|---|---|---|---|
| 體質與估值定位的當日百分位 | 不需要；只用當日目前參考值 | 相容：不建歷史、不進回測；百分位只放記憶體快取 | 第一期 |
| 同產業排名 | 同上 | 相容 | 第一期 |
| 技術面訊號統計（含 252 日新高） | 日 K | 不涉及基本面 | 第一期（20、60 日） |
| 外資連 5 買 | 法人逐日資料 | 不涉及基本面 | 觸發第一期；統計受 250 日上限限制 |
| 合理價、河流圖、本益比自身歷史位階 | 估值每日歷史 | 6G 不允許把沒有可用時間的估值當歷史使用 | 需 PD1、PD2 |
| 近 8 季財報、近 12 個月營收 | 財報與月營收歷史 | 同上 | 需 PD1（營收另需 PD2） |
| 月營收年增 ≥ 20% 的歷史勝率 | 營收歷史加事件可用時間 | finlab 用公告截止日後第一個交易日當事件日，屬 6G 明列拒絕的對應 | PD1 下仍不開放 |
| 估值分桶後續報酬、估值 × 動能 | 每日估值的可用時間 | 以歷史估值為條件計算之後的報酬，屬回測性質 | PD1 下仍不開放 |

### 4.3 PD1「僅供顯示的歷史層」的邊界（提案）

- 允許：回補後在個股頁顯示歷史值（河流圖、近 8 季、近 12 個月、自身歷史位階），並標示「回溯取得的歷史值，非當時可得」。
- 禁止：寫入 `financials/metrics/part.parquet`（回測財務因子會讀它，[backtest/fundamentals.py:50](../../backend/app/backtest/fundamentals.py#L50)）；供選股歷史、回測、比較頁的歷史比較日期或任何 as-of 查詢使用；給它填 `available_at`。
- 殘留風險：遲交或事後更正的公司，回溯取得的值可能和當時公布的不同。

## 5. 回補量估算

推論：以每年約 245 個交易日粗估，沒有扣除休市日的實際差異。

| 資料 | 每次請求涵蓋 | 範圍 | 請求數 |
|---|---|---|---|
| 估值（`BWIBBU_d` 加 `peQryDate`） | 一個交易日、一個市場的全市場 | 5 年 | 約 1,225 × 2 ≈ 2,500 |
| 同上 | 同上 | 全部可查期間（上市自 2005-09、上櫃自 2007-01） | 約 5,200 + 4,850 ≈ 10,000 |
| 月營收（`t21sc03`） | 一個月、一個市場 | 5 年 | 60 × 2 = 120；只顯示 12 個月則 12 × 2 = 24 |
| 報酬指數（MI_INDEX，若回補） | 一個交易日 | 自 2024-01-02（對齊日 K） | 約 700 |
| 法人加深（T86、上櫃法人） | 一個交易日、一個市場 | 5 年 | 約 2,500；融資融券另約 2,500。官方最早可查日期未查明 |
| 財報季資料 | — | 官方只有最新一季，不能回補 | 從現在起每季抓 24 份清單（6 類 × 損益與資產負債 × 2 市場） |
| 集保（開放資料） | 一週、全市場 | 只能從現在起累積 | 每週 1 次 |
| 集保（逐檔查詢回補） | 一檔、一週（推論） | 約 51 週 | 檔數 × 約 51；以 1,000 檔計約 51,000 次，不建議 |

以既有的 0.4 秒間隔計，2,500 次約 17 分鐘（不含回應時間）；實際要依節流情況調整，並支援中斷後續傳。

## 6. 第三方季度財報資料源查證摘要（2026-10-29）

PD2 的 C 案：查有授權、可用於回補的第三方來源。本節是查證摘要，尚未接任何第三方；方案 A 的存檔已預留 `source` 欄位區分來源。

| 來源 | 結論 | 限制 |
|---|---|---|
| FinMind | 首選 | 一次回補需付費方案；本地儲存與自架顯示的條款未明示，待使用者寫信確認 |
| FinLab | 條款寬鬆 | 免費版資料只到 2023-12-31；VIP 最少 12 個月 |
| TEJ | 未評估到可用 | 價格與條款不公開（<https://api.tej.com.tw/documents.html>、<https://tejtw.github.io/EN-TEJAPI>） |
| Fugle | 不適用 | 沒有財報資料（<https://developer.fugle.tw/docs/data/http-api/getting-started>） |
| CMoney | 不適用 | 沒有公開 API；查證時未取得 API 文件 URL，只找到產品頁 <https://www.cmoney.tw/app/ItemContent.aspx?id=5778> |

口徑注意：FinMind 的 `EquityAttributableToOwnersOfParent` 是「綜合損益總額歸屬於母公司業主」，不是本期淨利歸屬於母公司業主；若日後回補，不能填入本專案的 `parent_net_income`。

來源：

- <https://finmind.github.io/tutor/TaiwanMarket/Fundamental/>
- <https://finmind.github.io/en/Disclaimer/>
- <https://finlab.finance/terms>
