# G02-gap｜G2-fundamentals-etf-reference

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 4 | 2 | 0 | 0 | 0 | 16 | 9 |

## 稽核者摘要

結論：這個區塊沒有 P0～P2 等級的口徑錯誤。已驗證的部分都正確：月營收千元換成元、月增率與年增率以 % 數值顯示、民國年月換算、「累計 EPS」為年初至今累計、各季報表期間推算、股本三欄解析、ETF 基本資料與發行單位數。我用官方參考檔、手工組的資料列、Yahoo 與 HiStock 交叉核對過。共找到 6 項 P3：
(1) 個股比較頁的本益比列，台股個股永遠顯示「N/A（不適用）」；
(2) 自選清單與資訊列的 10 個財務欄位在台股部署永遠沒有資料，/api/financials/metrics 回 200 但內容是空的，不是 403；
(3) 財報卡的營收、營業利益、淨利是年初至今累計值，但標籤沒有標示「累計」；
(4) 外幣面額（TDR、KY 公司）顯示錯誤幣別，或被四捨五入成「0 元」；
(5) 財報 6 個類別中任一個請求失敗，整段就顯示讀取失敗，log 中 TPEx 12 次有 3 次中止；
(6) 沒有快取，每開一次頁就下載 10 份全市場資料。
無法驗證的部分主要有兩類：一是 app 對 5 檔自選股實際顯示的 PE、PB、殖利率、營收年增率與累計 EPS 數值，因為規定不得連官方；二是 TPEx 月營收與財報端點的欄位名稱。網路請求已用滿 30 次，WebSearch 在本 session 已被系統用罄。

## 問題清單

### [P3 → P3] 自選清單與資訊列的 10 個財務欄位（EPS、BPS、ROE、PE、PB、毛利率、淨利率、營收增速、淨利增速、負債率）在台股部署永遠沒有值（確認成立）

- **主張**：財務欄位組讀的是 A 股的 /api/financials/metrics，而 data/financials/metrics 是空的。能力 financial 被強制開啟，所以 API 回 200 加上空資料，不是 403。資訊列的空值會被靜默略過；自選表的財務欄位則是後端 enriched 根本不產生，永遠顯示「—」。另有潛在口徑問題：資訊列的 PE 用 close/eps_basic 計算（最新一期 EPS，不是近四季），而自選欄位標籤寫「PE(TTM)」
- **正確的定義或數值**：台股部署應隱藏或停用 A 股的財務欄位組，或改接台股官方資料：PE、PB、殖利率接 BWIBBU_ALL，營收增速接 t187ap05_L 的年增率
- **app 實際行為**：使用者在自選清單或資訊列打開這些欄位後，自選表顯示「—」，資訊列則完全不出現，而且沒有任何提示
- **對使用者的影響**：預設是隱藏的，所以一般情況看不到；使用者打開後會以為資料還在載入或是個股沒有資料。若日後補上資料，資訊列的 PE 會以單季或累季 EPS 計算，明顯偏高
- **修法建議**：台股部署下從欄位註冊表移除財務群組，或把 pe_ttm、pb、revenue_yoy 改接 TaiwanOfficialFundamentals 的 valuation 與 monthly_revenue
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/lib/watchlist-columns.ts:80-89,101（10 個財務欄位與財務群組）
  - frontend/src/lib/stock-info-fields.ts:34-44,51
  - backend/app/api/financials.py:38-49（空資料時回 {"data": []}）
  - backend/app/tickflow/policy.py:25（Cap.FINANCIAL 固定開啟）
  - frontend/src/components/StockPanel.tsx:84-90,133（有能力且欄位可見才請求，取 data[0]）
  - frontend/src/components/StockInfoBar.tsx:170-189,215-216（空值 return null，整個欄位略過；pe_ttm = close/eps_basic）
  - frontend/src/components/stock-table/primitives.tsx:143-153（r.eps 等欄位為 null 時顯示 '—'）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/2454.TW/eps（說明單季、累季、近四季 EPS 是不同口徑；以單季或累季 EPS 計算 PE 都不是 TTM）
- **本地計算證據**：
  - 在程序內呼叫 get_metrics：2454.TWSE、3034.TWSE、不帶 symbol 都回 {'data': []}；detect_capabilities().has(Cap.FINANCIAL)=True
  - data/financials/{metrics,income,balance_sheet,cash_flow,shares} 都是 0 個檔案
  - enriched 2026-09-23 分區欄位中沒有 eps、bps、roe、pe_ttm、pb 等財務欄；grep backend/app 找不到任何產生這些鍵的程式
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：在台股部署下，自選清單與資訊列的 10 個財務欄位（EPS、BPS、ROE、PE、PB、毛利率、淨利率、營收增速、淨利增速、負債率）永遠沒有值。原因有三：
- data/financials/* 全部是 0 個檔案，而且 financial_sync.py 已經不再有程式寫入。
- Cap.FINANCIAL 被固定開啟，所以 /api/financials/metrics 對任何 symbol 都回 200 加 {"data": []}，不是 403。
- enriched 的 661 個分區都沒有任何財務欄。

結果是資訊列的財務欄位被靜默略過，自選表的財務欄位固定顯示「—」，而且沒有任何提示。這些欄位預設隱藏，使用者目前保存的設定也都是隱藏，所以現在沒有實際影響。

潛在口徑問題：資訊列的 PE（標籤是「PE」，程式碼註解已承認「非嚴格 TTM」）用 close/eps_basic 計算。以 2454 在 2026-09-23 的收盤價 5185 為例，用單季 EPS 會得到 339.33，用上半年累季 EPS 會得到 170.28，分別是 TTM 口徑 85.43 的 3.97 倍與 1.99 倍；Yahoo 顯示的本益比是 85.63。自選表的「PE(TTM)」讀的是後端的 r.pe_ttm，並沒有用這個公式，兩者只是共用 key 名稱 pe_ttm。這個問題要等日後有人接上資料源才會浮現。
    - 理由：我用自己寫的腳本重新跑過一次，finding 的核心說法全部重現。
(1) data/financials 底下 5 個子目錄全都是 0 個檔案。
(2) detect_capabilities().has(Cap.FINANCIAL)=True。在程序內呼叫 get_metrics，2454.TWSE、3034.TWSE、0050.TWSE 與不帶 symbol 四種情況都回 {'data': []}，沒有丟出 CapabilityDenied，所以前端拿到的是 200 加空資料，不是 403。
(3) kline_daily_enriched 共 661 個 parquet，我掃了全部的 schema，eps、bps、roe、pe_ttm、pb、gross_margin、net_margin、revenue_yoy、net_income_yoy、debt_ratio、eps_basic、debt_to_asset_ratio 在任何分區都沒有出現。2026-09-23 分區有 72 欄，財務欄為 0 個。grep backend/app 也只找到 backtest/fundamentals.py 在讀 metrics，沒有任何程式產生這些鍵。
(4) 前端行為與 finding 所述一致：StockPanel 只在財務欄位可見且有 financial 能力時才發請求，然後取 data[0]。StockInfoBar 遇到空值就 return null，整個欄位不顯示。自選表 primitives 遇到 null 則顯示「—」。欄位選單沒有任何財務能力或資料缺失的提示。
(5) 使用者情境：preferences.json 裡已保存的 49 個自選欄位中，10 個財務欄全部 visible=False，因此目前使用者看不到這個問題，與「預設隱藏」的影響描述相符。資訊列的欄位設定存在瀏覽器 localStorage，我無法查證。

PE 口徑的潛在問題我也做了數值重現。Yahoo 奇摩的 2454 單季 EPS 為 2026Q2 15.28、Q1 15.17、2025Q4 14.40、Q3 15.84，近四季合計 60.69。官方 STOCK_DAY 參考資料中，9/23 收盤價為 5185。三種口徑算出的 PE：
- close/TTM = 85.43，Yahoo 頁面顯示的本益比為 85.63，兩者吻合。
- close/單季 = 339.33，是 TTM 口徑的 3.97 倍。
- close/2026 上半年累季 = 170.28，是 TTM 口徑的 1.99 倍。
所以資訊列的公式 close/eps_basic 一旦有資料，確實會明顯偏高。

但有兩點敘述需要修正或補充：
- 資訊列 PE 的標籤是「PE」而不是「PE(TTM)」（stock-info-fields.ts:38），而且程式碼註解 StockInfoBar.tsx:179 已經承認「非嚴格 TTM」。自選表的「PE(TTM)」讀的是後端 row 的 r.pe_ttm，不是 close/eps_basic。兩者只是共用 key 名稱 pe_ttm，並不是同一個畫面上標錯。
- financial_sync.py:8-9 寫明 metrics「不再有代碼寫入新數據」，所以 PE 口徑問題目前完全是潛在的，要有人新接資料源才會發生。
- 引用行號小誤：primitives 的財務 case 在 142-151 行，不是 143-153 行。
以上都不影響結論。嚴重度維持 P3（局部體驗，預設隱藏）。

另有一個 finding 沒提到的相關面向，我只驗到資料層：backend/app/backtest/fundamentals.py 的 7 個財務因子（pb_latest、roe_latest 等）同樣讀 data/financials/metrics，所以在台股部署一樣沒有資料。這些因子在 UI 上是否可選，我沒有驗證，屬推測範圍。

工作樹比對：git status 與開始時相同，仍是原本 3 個已修改檔。我沒有寫入 repo 或 data。
    - 證據：
      - 本地計算：cd backend && uv run python AUDIT_SCRATCH/work/verify-g2fin/verify.py → balance_sheet/cash_flow/income/metrics/shares 各 0 files；settings.data_dir=data；has(Cap.FINANCIAL)=True；get_metrics(2454.TWSE/3034.TWSE/0050.TWSE/None) 全部 -> {'data': []}
      - 本地計算（同一腳本）：kline_daily_enriched 共 661 個 parquet，財務鍵命中數 {}，所有分區的欄位聯集 72 欄；date=2026-09-23 分區 72 欄，財務欄 []；kline_etf_enriched 與 kline_index_enriched 都是 0 個 parquet
      - 本地計算（同一腳本）：preferences.json 的 watchlist_columns 共 49 欄，builtin:eps/bps/roe/pe_ttm/pb/gross_margin/net_margin/revenue_yoy/net_income_yoy/debt_ratio 全部 visible=False；financial_data_provider=taiwan
      - backend/app/api/financials.py:25-28,44-46（有 Cap.FINANCIAL 就放行；df 為空時回 {"data": []}）
      - backend/app/tickflow/policy.py:25（Cap.FINANCIAL 無條件開啟）
      - backend/app/services/financial_sync.py:8-9,33-37（不再有程式寫入新資料；part.parquet 不存在時回空 DataFrame）
      - frontend/src/components/StockPanel.tsx:84-90,133；StockInfoBar.tsx:171-187,215-216（空值 return null，整個欄位跳過；pe_ttm = close / eps_basic；179 行註解「PE 基於最新一期 EPS，非嚴格 TTM」）
      - frontend/src/components/stock-table/primitives.tsx:142-151（r.eps 等欄位為 null 時顯示 '—'；finding 寫 143-153 是小誤）
      - frontend/src/lib/watchlist-columns.ts:80-89,83,101（自選表 pe_ttm 標籤為 'PE(TTM)'）；stock-info-fields.ts:35-44,38,51（資訊列 pe_ttm 標籤為 'PE'，不是 PE(TTM)）
      - 線上：https://tw.stock.yahoo.com/quote/2454.TW/eps → 單季 EPS 2026Q2 15.28、2026Q1 15.17、2025Q4 14.4、2025Q3 15.84；頁面定義：近四季每股盈餘總和＝最近四季單季 EPS 相加
      - 線上：https://tw.stock.yahoo.com/quote/2454.TW → 本益比 85.63，收盤價 5,185.00（2026/09/23 14:30）
      - 本地計算：uv run python ...\verify-g2fin\pe_scale.py → 官方參考資料 STOCK_DAY 中 2454 在 2026-09-23 收盤 5185.0；TTM EPS 60.69 → PE 85.43（對照 Yahoo 85.63）；H1 累季 EPS 30.45 → PE 170.28（1.99x）；單季 EPS 15.28 → PE 339.33（3.97x）
      - backend/app/taiwan/fundamentals.py:179-193,231-243（TaiwanOfficialFundamentals.monthly_revenue 接 t187ap05_L；valuation 接 BWIBBU_ALL，修正建議所指的資料來源確實存在）
      - 附帶面向（只驗到資料層）：backend\app\backtest\fundamentals.py:31-39 的 7 個財務因子同樣讀 data/financials/metrics，在台股部署也沒有資料

### [P3 → P3] 財報卡的營業收入、營業利益、本期淨利是年初至今累計值，標籤卻沒有標示「累計」，也只顯示期末日（確認成立）

- **主張**：t187ap06_L 的 Q2 是 1～6 月累計、Q4 是全年。app 把 period_start 設為 1 月 1 日，但畫面只顯示「報表期間：期末日」。EPS 標了「累計」，同一張卡的營收、營業利益、淨利卻沒有標
- **正確的定義或數值**：標示「營業收入（累計）」，或顯示「報表期間 2026-01-01～2026-06-30」
- **app 實際行為**：只顯示「報表期間 2026-06-30」與「營業收入 X 億元」
- **對使用者的影響**：目前最新一期是 2026Q2。使用者若把上半年營收與淨利當成 Q2 單季，會高估約一倍；與其他網站的單季 EPS（例如 2454 的 15.28）對照時也會混淆
- **修法建議**：在前端顯示 period_start～period_end，並把三個金額的標籤加上「（累計）」
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/fundamentals.py:219-229（revenue、operating_income、net_income 取自同一份累計損益表；period_start 為 f"{year}-01-01"）
  - frontend/src/components/taiwan/TaiwanReferenceData.tsx:154-162（標籤為「報表期間」period_end、「營業收入」「營業利益」「本期淨利」、「累計 EPS」）
- **線上證據**：
  - https://raw.githubusercontent.com/lulu930128/open-market-intelligence/main/backend/tests/test_financial_metric_semantics.py（Q2 為 ytd、months_covered 6；Q4 為 annual、months_covered 12）
  - https://tw.stock.yahoo.com/quote/2454.TW/eps（2026Q2 單季 EPS 15.28，Q1 15.17）
- **本地計算證據**：
  - v2.out：季別=2 時 period_start=2026-01-01、period_end=2026-06-30
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：原敘述成立，以真實數據重現並補充如下。上市 t187ap06_L 與上櫃 mopsfin_t187ap06_O 的營業收入、營業利益、本期淨利、EPS 都是年初至今累計值，Q4 是全年值。app 正確地把 period_start 設為當年 1 月 1 日，但財報卡（TaiwanReferenceData.tsx:156-159）只顯示期末日，三個金額也沒有標「累計」。以最新的 2026Q2 為例，2454 會顯示營業收入 3,013.33 億元，實際上是 Q1 的 1,491.51 億元加上 Q2 的 1,521.83 億元；使用者若當成 Q2 單季，會高估約 1.98 倍，營業利益與淨利約 2.0 倍、EPS 30.44 對 15.28 約 1.99 倍。倍數會隨季度增加：Q3 約 3 倍，Q4 是全年值，約 4 倍。上市與上櫃都有此問題。
    - 理由：finding 成立，我以真實數據重現了它的數值差異。原稽核的本地證據 v2.out 用的是手工假資料（營收 300000000000），只能證明程式行為，所以我改用真實官方資料重算。資料來自 Wayback Machine 封存的 MOPS 公開資料，沒有直接連證交所或櫃買中心。

一、資料口徑：t187ap06 各季數值是年初至今累計，Q4 是全年。
1. t187ap06_L_ci 113Q3（出表日期 1131103）：聯電 2303 的 EPS 是 3.12。Yahoo 單季 EPS 為 2024Q1 0.84、Q2 1.11、Q3 1.16，三季合計 3.11，對得上 1～9 月累計；單季 Q3 只有 1.16。
2. mopsfin_t187ap06_O_ci 113Q4（出表日期 1140413）：世界先進 5347 的 EPS 是 4.21。Yahoo 2024Q4 單季 EPS 只有 1.1，四季合計 4.28，可見 4.21 是全年值。這也表示上櫃也有同樣問題。
3. 2026Q2 的 t187ap06_L 沒有封存版本，改用同一批 MOPS 彙總的 t187ap14_L（出表日期 1150917，年度 115、季別 2）。2454 的營業收入 301,333,121 千元，等於 Yahoo 單季 Q1 149,150,510 加 Q2 152,182,611；營業利益 45,758,626、稅後淨利 48,981,437 也都等於兩季相加，一元不差。另外，自選 6 檔個股的 115Q2 營業收入，都等於 t187ap05_L 11507 的「累計營收減當月營收」，也就是 1～6 月合計，比值全部是 1.0000。
4. 封存的 CSV 欄位名稱和 app 讀的欄位（營業收入、營業利益（損失）、本期淨利（淨損）、基本每股盈餘（元））完全相同，缺少清單為空。

二、程式行為：我把上面的真實資料列透過 MockTransport 餵給 app 的 TaiwanOfficialFundamentals.financial_statement。2454 得到 period_start=2026-01-01、period_end=2026-06-30，營收 301,333,121,000 元，照前端 money() 格式化後顯示「3,013.33 億元」。前端 TaiwanReferenceData.tsx:156 只顯示 period_end；157～159 三個金額沒有標「累計」，只有 160 的 EPS 標了。current_data.py 雖然把完整 record（含 period_start）放進 data，前端卻沒有顯示。

三、數值對照：finding 說「高估約一倍」，重算結果是 2454 營收 1.980 倍、營業利益 2.001 倍、淨利 1.991 倍、EPS 30.44 對 15.28 為 1.992 倍，重現成立。補充一個原 finding 沒提到的面向：倍數會隨季度變大，Q3 約 3 倍（2303 的 EPS 3.12 對 1.16 為 2.69 倍），Q4 是全年值，約 4 倍（5347 為 3.83 倍）。

嚴重度：數值本身正確，問題在標示不清，屬於局部體驗，依 CONTRIBUTING.md:297 維持 P3。docs/code-review-2026-09-22.md 沒有「財報、營業收入、報表期間」等相關記錄，這不是已知問題。稽核過程只寫入 scratchpad，git status 與開始時相同。
    - 證據：
      - backend/app/taiwan/fundamentals.py:219-229：revenue、operating_income、net_income、cumulative_eps 取自同一筆 t187ap06 資料列，period_start 固定為 f"{year:04d}-01-01"
      - frontend/src/components/taiwan/TaiwanReferenceData.tsx:156-160：「報表期間」只顯示 period_end；「營業收入」「營業利益」「本期淨利」沒有標累計；只有「累計 EPS」有標
      - backend/app/taiwan/current_data.py:249-259：data=_json_value(record) 含 period_start，但前端沒有使用；frontend/src/pages/TaiwanStockDetail.tsx:779 會在非指數標的上渲染此卡片
      - Wayback 封存 https://web.archive.org/web/20241102233451id_/https://mopsfin.twse.com.tw/opendata/t187ap06_L_ci.csv：年度 113、季別 3，2303 聯電的營業收入為 171916474.00，基本每股盈餘（元）為 3.12
      - https://tw.stock.yahoo.com/quote/2303.TW/eps：「2024 Q1 | 0.84」「2024 Q2 | 1.11」「2024 Q3 | 1.16」，三季合計 3.11，對得上 CSV 的 3.12（累計）；單季 Q3 為 1.16
      - Wayback 封存 https://web.archive.org/web/20250413170319id_/https://mopsfin.twse.com.tw/opendata/t187ap06_O_ci.csv：年度 113、季別 4，5347 世界先進的營業收入為 44054762.00，EPS 為 4.21；https://tw.stock.yahoo.com/quote/5347.TWO/eps 的 2024 Q4 單季 EPS 為 1.1，可見 Q4 是全年值
      - Wayback 封存 https://web.archive.org/web/20260917153011id_/https://openapi.twse.com.tw/v1/opendata/t187ap14_L：出表日期 1150917，2454 年度 115、季別 2，營業收入 301333121.00、營業利益 45758626.00、稅後淨利 48981437.00、基本每股盈餘 30.44
      - https://tw.stock.yahoo.com/quote/2454.TW/income-statement：2026 Q2 營業收入 152,182,611、營業利益 22,867,781、稅後淨利 24,605,053；2026 Q1 分別為 149,150,510、22,890,845、24,376,384（仟元）。兩季相加得 301,333,121、45,758,626、48,981,437，與 t187ap14_L 完全相等
      - https://tw.stock.yahoo.com/quote/2454.TW/eps：「2026 Q2 | 15.28」「2026 Q1 | 15.17」，合計 30.45，約等於 30.44
      - 本地計算 verify-g2ytd/compare.py → compare.out：自選 6 檔（2454、2330、3034、2379、6196、3545）的 ap14 115Q2 營業收入，都等於 ap05 11507 的「累計營收減當月營收」，比值全部是 1.0000
      - 本地重現 verify-g2ytd/repro_app.py → repro_app.out：2454 經 app financial_statement 處理後得到 period_start=2026-01-01、period_end=2026-06-30；卡片會顯示「報表期間 2026-06-30」「營業收入 3,013.33 億元」「營業利益 457.59 億元」「本期淨利 489.81 億元」「累計 EPS 30.44」
      - 倍數：2454 營收 301,333,121/152,182,611=1.980、營業利益 2.001、淨利 1.991、EPS 30.44/15.28=1.992；2303 Q3 的 EPS 3.12/1.16=2.69；5347 Q4 的 EPS 4.21/1.1=3.83
      - 腳本與輸出位於 AUDIT_SCRATCH/work/verify-g2ytd/

### [P3 → P3] 股本卡把外幣面額當成新台幣顯示，小面額還會四捨五入成「0 元」；TDR 的股數是原股數（確認成立）

- **主張**：_par_value_twd 只抓第一個數字，不看幣別；前端固定加上「元」並取 2 位小數。TDR 與 KY 公司的面額是美元、泰銖或港幣，會顯示錯誤。TDR 的「已發行普通股數」其實是官方的「TDR原股發行股數」
- **正確的定義或數值**：面額要顯示原幣別（例如「USD 0.001」），或在不是新台幣時顯示原文；TDR 的股數標籤應為「TDR 原股發行股數」
- **app 實際行為**：4157 顯示「每股面額 0 元」；9103 顯示「0.05 元」，實際是美元 0.05
- **對使用者的影響**：只影響 TDR 與 KY 等約 11 檔；自選 9 檔都不受影響
- **修法建議**：_par_value_twd 遇到非「新台幣」字樣就回傳 None 並保留原文，前端顯示原文；TDR 改用正確的股數標籤，市值與換手率排除 TDR 或換算轉換比例
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/fundamentals.py:53-58
  - frontend/src/components/taiwan/TaiwanReferenceData.tsx:175-177（「已發行普通股數」；「每股面額」為 `${number(par)} 元`，最多 2 位小數）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 官方 t187ap03_L／mopsfin_t187ap03_O 中有 10 筆非新台幣面額：9103 為「美金0.05元」、910322 為「美金0.0050元」、9105 為「泰銖1元」、910861 為「港幣0.1元」、911868 為「HKD 0.1」、4157 太景*-KY 為「美元 0.0010元」等（v1.out）
  - node 重現：面額 0.001 顯示為「0 元」，0.05 顯示為「0.05 元」
  - security_master.parquet：9103、910322、9105、911608、4157 的 instrument_type 都是 stock，所以會進入個股參考資料卡
  - share_capital.parquet 也收錄 TDR 的原股數，例如 910322 為 5,620,600,000、9105 為 10,450,002,831；N1 的市值與換手率以此為分母，推測 TDR 的市值與換手率口徑有誤（沒有查證 TDR 轉換比例，交由市值與換手領域覆核）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：確認成立，另外補充兩個細節。

`_par_value_twd`（fundamentals.py:53-58）只抓第一個數字、不看幣別；前端（TaiwanReferenceData.tsx:177）固定加上「元」，並取最多 2 位小數。官方公司基本資料有 10 列面額不是新台幣：
- 7 列寫明外幣：9103 美金0.05、910322 美金0.0050、9105 泰銖1、910861 港幣0.1、911622 泰銖7.5、911868 HKD 0.1、4157 美元0.0010。
- 3 列只有數字、沒寫幣別：9110「0.01」、912000「0.1」、9136「0.1」。

這 10 列都會被當成新台幣顯示。4157 顯示「0 元」；910322 顯示「0.01 元」，實際是美金 0.005，幣別錯之外，數值也被進位放大一倍。

911608「不適用」、6969／7812「無面額」解析為 None，顯示「--」，這幾列沒有錯。

10 檔 TDR 的 security_master 類型都是 stock，會出現股本卡。卡片上「已發行普通股數」的官方原欄位是「已發行普通股數或TDR原股發行股數」，所以 TDR 顯示的是原股數，例如 910322 為 5,620,600,000、9105 為 10,450,002,831。受影響共 11 檔（10 檔 TDR 加 4157），自選 9 檔都不受影響。

TDR 市值與換手率以原股數為分母的問題不在本次驗證範圍。已有佐證：Wikipedia 記載康師傅 TDR 為二股 TDR 換一母股，但仍待市值與換手率領域覆核。
    - 理由：我用官方參考資料與本地 parquet 重算後，finding 列出的數字全部重現，另外找到兩個它沒寫到的細節。

1. 解析：我直接呼叫 repo 的純函式 `_par_value_twd`，輸入官方原文，結果與 finding 所述一致：9103「美金0.05元」得 0.05、910322「美金0.0050元」得 0.005、9105「泰銖1元」得 1.0、910861「港幣0.1元」得 0.1、911868「HKD 0.1」得 0.1、4157「美元 0.0010元」得 0.001、911622「泰銖7.5元」得 7.5。函式只抓第一個數字，不看幣別。

2. 前端：我在 node 用同一段 `number()`（`toLocaleString` 最多 2 位小數）加上「 元」重現，4157 顯示「0 元」，9103 顯示「0.05 元」，與 finding 一致。它沒提到的是 910322 的實際面額為美金 0.005，畫面上卻顯示「0.01 元」，不但幣別錯，數值也被進位放大一倍。

3. 筆數：官方兩個資料集共 1,988 列，其中 13 列不是以「新台幣」開頭。含數字的有 10 列，也就是 finding 說的 10 筆：7 列寫明外幣，另有 9110「0.01」、912000「0.1」、9136「0.1」這 3 列只有數字、沒寫幣別。其餘 3 列不含數字（6969、7812「無面額」，911608「不適用」），解析結果是 None，畫面顯示「--」，這幾列沒有錯。

4. 影響範圍：官方有 10 檔 -DR，在 security_master 的 instrument_type 全是 stock。後端依 instrument_type 決定走公司資料流程（current_data.py:191-194），前端只要不是 etf 就顯示個股卡片（TaiwanReferenceData.tsx:258-260），所以這些標的都會出現股本卡。KY 公司共 122 列，只有 4157 用外幣面額。受影響共 11 檔（10 檔 TDR 加 4157），與 finding 所述相同，自選 9 檔都不在其中。

5. TDR 股數：官方欄位的原名就是「已發行普通股數或TDR原股發行股數」，後端直接取用這個欄位（fundamentals.py:254-256），前端卻標成「已發行普通股數」（TaiwanReferenceData.tsx:175）。share_capital.parquet 的數字與 finding 相同：910322 為 5,620,600,000，9105 為 10,450,002,831。

6. finding 列為「推測」、交給市值與換手率領域覆核的部分，我找到一些佐證，但這不是本次要驗證的範圍。Wikipedia 提到康師傅 TDR「以二股TDR換一母股比例」。若照這個比例，9/23 收盤 23.05 × 5,620,600,000 算出約 1,295.5 億元，會把母公司市值低估一半。9/23 的 TDR 成交 12,747 股除以原股數，得到的換手率是 0.000227%。

7. par_value_twd 沒有其他後端計算在用，只影響畫面顯示，所以維持 P3。

另外有一點我沒有查證（推測）：911868 的實收資本額 209,446,542 剛好等於原股數 × 0.1，看起來是港幣金額沒有換算成新台幣，卡片卻當作新台幣顯示。
    - 證據：
      - backend/app/taiwan/fundamentals.py:53-58 `_par_value_twd` 用 re.search 取第一個數字，不看幣別
      - backend/app/taiwan/fundamentals.py:254-262 issued_shares 取自官方欄位「已發行普通股數或TDR原股發行股數」／IssueShares；par_value_twd 取自「普通股每股面額」／ParValueOfCommonStock
      - frontend/src/components/taiwan/TaiwanReferenceData.tsx:175 標籤寫「已發行普通股數」；:177 顯示 `${number(par_value_twd)} 元`；:56-60 number() 的 maximumFractionDigits 預設為 2
      - backend/app/taiwan/current_data.py:191-194 依 security_master 的 instrument_type 走公司資料流程；TaiwanReferenceData.tsx:258-260 只要不是 etf 就用 StockSections
      - 本地計算：`cd backend && uv run python AUDIT_SCRATCH/work/verify-parvalue/v1.py`（輸出在 v1.out）。官方 TWSE 1095 列、TPEx 893 列；面額文字型態（去掉數字後）：新台幣元 1975、無面額 2、美金元 2、泰銖元 2、港幣元 1、純數字 3、不適用 1、HKD 1、美元元 1
      - v1.out 裡 _par_value_twd 的實際輸出：9103 美金0.05元→0.05；910322 美金0.0050元→0.005；9105 泰銖1元→1.0；910861 港幣0.1元→0.1；911622 泰銖7.5元→7.5；911868 HKD 0.1→0.1；9110 '0.01'→0.01；912000 '0.1'→0.1；9136 '0.1'→0.1；4157 美元 0.0010元→0.001；6969／7812 無面額→None；911608 不適用→None
      - node v24.14.1（ICU 78.2）用同一段 number() 重現（fmt.js）：4157 '0 元'、910322 '0.01 元'、9103 '0.05 元'、9105 '1 元'、911622 '7.5 元'、2454（新台幣 10）'10 元'
      - 官方 -DR 共 10 列（9103、910322、9105、910861、9110、911608、911622、911868、912000、9136），security_master.parquet 中這 10 檔的 instrument_type 都是 stock；KY 共 122 列，只有 4157 的面額不是新台幣；4157.TPEX 的 instrument_type 也是 stock
      - share_capital.parquet：910322 為 5,620,600,000，9105 為 10,450,002,831，與官方 raw JSON 的「已發行普通股數或TDR原股發行股數」一致；Yahoo 奇摩 https://tw.stock.yahoo.com/quote/910322.TW/profile 同樣顯示已發行普通股數 5,620,600,000、股本 916,513,457
      - v2.py／v2.out：910322 在 9/23 的收盤 23.05（本地與官方 MI_INDEX 相同），乘以 5,620,600,000 得 129,554,830,000；成交 12,747 股除以原股數得換手率 0.000227%
      - https://zh.wikipedia.org/zh-tw/臺灣存託憑證 原文：「以二股TDR換一母股比例，等於以1.9億股的香港康師傅來台增資」（佐證 TDR 的單位數與原股數不同；屬於市值與換手率領域）
      - git status --short 與開始時相同，repo 沒有新增任何改動

### [P3 → P3] 財報的 6 個類別中任一個請求失敗（例外不是 LookupError），整張財報卡就顯示讀取失敗；log 中 TPEx 12 次有 3 次中止（確認成立）

- **主張**：financial_statement 逐一抓 6 個全市場類別，只吞 LookupError。任何傳輸或 JSON 例外都會中止整段，在 current_data 變成 status=error，前端顯示「此項官方資料暫時無法讀取」
- **正確的定義或數值**：單一類別暫時失敗時要重試或跳過，找到符合的類別後就停止，不讓整張卡失敗
- **app 實際行為**：推測是傳輸或 JSON 解析暫時失敗（log 沒有記錄例外），使整張財報卡顯示錯誤
- **對使用者的影響**：上櫃股的詳細頁約有四分之一的機率看不到財報卡；自選全部是上市股，影響小
- **修法建議**：對每個類別的非 LookupError 做有限重試，或用快取的類別對照表只抓該公司所屬的類別；current_data 捕捉到例外時寫 WARNING log
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/taiwan/fundamentals.py:201-210（只 except LookupError；目標公司只在其中一類，其餘 5 類的下載都是多餘的）
  - backend/app/taiwan/current_data.py:262-269（例外轉成 status=error，不寫 log）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 從 backend.log、backend.log.1、backend.log.3 抽出請求序列：TPEx 的 mopsfin_t187ap06_O_ci 共 12 次，其中 3 次（2026-09-22 15:42:46、16:11:37、18:00:19）之後沒有 basi 以後的類別，直接跳到 tpex_mainboard_peratio_analysis，也就是估值段，表示財報段已經中止；basi、bd、fh、ins、mim 各只有 9 次（G02-gap/fund_seq.txt）
  - TWSE 端 118 次沒有真正的中止；程式判到的 2 次是並行請求交錯造成的誤判
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：financial_statement 依序抓 6 個全市場類別，只吞 LookupError；資產負債表（ap07）的請求完全沒有保護。只要任一請求在收到回應前發生傳輸錯誤、回應內容讀取／JSON 解析失敗、HTTP 非 2xx，或回傳內容不是 list，整段就會中止。current_data 接著把它轉成 status=error，不寫 log，前端就顯示「此項官方資料暫時無法讀取」。

log 涵蓋 2026-09-17 11:14 至 09-24 06:19。期間 TPEx 財報查詢共 12 次，其中 3 次在 ci 回 200 OK 後直接跳到估值（peratio），時間都在 09-22：15:42:46、16:11:37、18:00:19。這三次都沒有非 200 回應，ci 到 peratio 的間隔也只有 0.56、3.4、4.2 秒，所以可以排除 HTTP 狀態錯誤與 30 秒逾時。剩下的可能是 basi 在收到回應前連線失敗，或 ci 的回應內容解析失敗；log 無法再細分（推測）。TWSE 端 118 次查詢中止 0 次。

3/12 的中止全部集中在 9/22（當日 8 次中 3 次），樣本太小，不宜外推成「上櫃股約四分之一機率」。使用者自選 9 檔都是上市股，影響小。
    - 理由：finding 的數字全部重現。機制方面，我用不連網的 MockTransport 注入錯誤，也重現出與 log 一模一樣的請求序列。

程式碼：
- fundamentals.py:201-206 逐一抓 6 個類別，只 except LookupError。
- 例外可能來自三處：_rows（fundamentals.py:337-342）的 raise_for_status、response.json()，以及非 list 時丟出的 ValueError；另外 client.get 的傳輸錯誤也會往上拋。這些例外都會中止整段。
- 資產負債表的請求（:210）不在 try 裡。
- current_data.py:262-269 用 except Exception 把例外轉成 status=error，不寫 log；current_data.py 與 fundamentals.py 都沒有 logger。
- 前端 TaiwanReferenceData.tsx:96 在 status==='error' 時，於 :116-119 顯示「此項官方資料暫時無法讀取，其他區塊不受影響。」

log 數據與 finding 的數字並列（自行重算）：
- TPEx：ci 12 次；basi、bd、fh、ins、mim 各 9 次；ap07_O_ci 9 次；peratio 12 次。finding 寫 12／9，一致。
- 中止時刻：2026-09-22 15:42:46,585、16:11:37,070、18:00:19,590。這三筆都是 ci 回 200 OK 後直接出現 peratio，再接 ap03。與 finding 的 3 筆一致。
- TWSE：ci 與另外 5 類各 118 次；資產負債表 118 次（ci 117 次、ins 1 次）；中止 0 次。與 finding 的「118 次沒有真正的中止」一致。
- 全部 TPEx／TWSE openapi 回應都是 200 OK。

重現實驗（repro_abort.py）：
- 正常情況：序列為 ap05 → ci → basi…mim → ap07_ci → peratio → ap03。
- 在 basi 注入 RemoteProtocolError 或 ConnectError：httpx 的 log 只剩 ap05 → ci → peratio → ap03，與實際 log 完全相同。原因是 httpx 0.28.1 在 _client.py:1025-1032 收到回應標頭後才寫 log，傳輸錯誤不會留下 log。
- 讓 ci 的回應內容是殘缺 JSON 或 HTML：出現同樣的序列。
- 以上三種情況的 financial_statement 都是 status=error；月營收、估值、股本三段不受影響。
- 若 basi 回 503，log 會多一行「basi 503」。實際 log 沒有這行，所以可以排除 HTTP 狀態錯誤。
- 三次中止中，ci 到 peratio 只間隔 0.56 秒、3.4 秒、4.2 秒，遠小於 30 秒逾時，所以也排除逾時。
- 剩下的可能原因只有兩種：basi 在收到回應前連線失敗，或 ci 的回應內容讀取／解析失敗。log 無法再細分，finding 將這部分標為「推測」是恰當的。

需要修正的地方只有影響估計：
- 「約四分之一」來自 12 次樣本。3 次中止全在 9/22（當日 8 次中 3 次）；9/18 的 2 次與 9/23 的 2 次都正常。
- log 涵蓋 9/17 11:14 至 9/24 06:19（9/22 14:54–15:01 有空檔）。
- 所以這只是觀測到的比率，不宜當成穩定機率。
- 使用者自選 9 檔全是 .TWSE，TWSE 端 0/118，實際影響小。維持 P3。

「其餘 5 類的下載都是多餘的」：參考資料沒有 t187ap06_O，無法用官方資料核對。不過 9 次完整的 TPEx 查詢，資產負債表都抓 ap07_O_ci，符合「只有 ci 一類命中」。另外程式在 :207-208 要求 matches 恰好為 1，這個設計目前靠多抓其他 5 類來確認唯一性。
    - 證據：
      - backend/app/taiwan/fundamentals.py:22 STATEMENT_CATEGORIES = ("ci", "basi", "bd", "fh", "ins", "mim")
      - backend/app/taiwan/fundamentals.py:201-208 for category ...: try: self._company_row(...) except LookupError: continue；if len(matches) != 1: raise LookupError
      - backend/app/taiwan/fundamentals.py:210 的資產負債表請求不在 try 內
      - backend/app/taiwan/fundamentals.py:335-343 _rows：client.get → raise_for_status() → response.json() → 非 list 時 raise ValueError；沒有重試、沒有快取
      - backend/app/taiwan/current_data.py:262-269 except Exception → status="error"，reason=type(exc).__name__；整個檔案沒有 logger／logging
      - backend/app/taiwan/current_data.py:173-178, 204-213 公司資料依序抓 monthly_revenue → financial_statement → valuation → share_capital_record（同步執行，不並行）
      - frontend/src/components/taiwan/TaiwanReferenceData.tsx:96, 116-119 status==='error' 時顯示「此項官方資料暫時無法讀取，其他區塊不受影響。」
      - backend/.venv/Lib/site-packages/httpx/_client.py:1013-1032（httpx 0.28.1）：transport.handle_request 回傳後才寫 'HTTP Request:' log，傳輸錯誤不會留下 log
      - 本地計算：grep 'tpex.org.tw/openapi' data/backend.log.3、backend.log.1、backend.log | uniq -c → mopsfin_t187ap06_O_ci 12；basi、bd、fh、ins、mim 各 9；mopsfin_t187ap07_O_ci 9；tpex_mainboard_peratio_analysis 12；mopsfin_t187ap05_O 12（輸出：work/verify-G2-fsabort/tpex_openapi_lines.txt）
      - 本地 log 序列 data/backend.log:1917-1920（15:42:45 ap05 → 15:42:46,585 ci 200 OK → 15:42:47,141 peratio → ap03）、:2166-2169（16:11:37,070 ci → 16:11:40,466 peratio）、:23703-23706（18:00:19,590 ci → 18:00:23,744 peratio），三次都跳過 basi～mim 與 ap07
      - 本地計算：grep 'openapi.twse.com.tw/v1/' 三個 log | uniq -c → t187ap06_L_ci、basi、bd、fh、ins、mim 各 118；t187ap07_L_ci 117 加 t187ap07_L_ins 1，共 118 → TWSE 中止 0 次；三個 log 的 TWSE／TPEx openapi 回應全部是 'HTTP/1.1 200 OK'
      - 重現腳本 AUDIT_SCRATCH/work/verify-G2-fsabort/repro_abort.py（MockTransport，不連網），輸出在同目錄 repro_output.txt：fault=basi_disconnect／basi_connect／ci_bad_json／ci_html 的 httpx log 都是 'ap05 > ci 200 OK > peratio > ap03'，financial_statement.status=error（reason 分別為 RemoteProtocolError／ConnectError／JSONDecodeError），其他三段不受影響；fault=basi_503 會多一行 'basi 503'，與實際 log 不符 → 可排除 HTTP 狀態錯誤
      - log 涵蓋範圍：backend.log.3 2026-09-17 11:14:36 → 16:46:10；backend.log.1 09-17 16:47:34 → 09-22 14:54:39；backend.log 09-22 15:01:18 → 09-24 06:19:41。TPEx 查詢分布：9/18 2 次、9/22 8 次（中止 3 次）、9/23 2 次（兩個請求並行，都完整）
      - 實驗前後 git status --short 相同，data\ 在實驗期間沒有新檔（backend.log 除外）

### [P3 → P3] 個股比較頁的本益比列，台股個股永遠顯示「N/A（不適用）」（部分成立）

- **主張**：research_context 對個股把 fundamentals_context 寫死為 unavailable，而且不帶任何數值。比較頁用 fmtNum(null) 顯示「N/A（不適用）」，把「沒有接上資料」說成「不適用」；AI 研究報告也永遠把 fundamentals_context 列為缺漏
- **正確的定義或數值**：個股的本益比要嘛顯示官方目前參考值（BWIBBU_ALL 的 PEratio，並標示為目前參考、不供歷史回測），要嘛標示「無資料／未接入」
- **app 實際行為**：自選 5 檔個股在比較頁的本益比永遠顯示「N/A（不適用）」，與 ETF 的「ETF 不適用」並列，讀起來像是個股本身沒有本益比
- **對使用者的影響**：使用者比較 2454、3034、2379 等個股時，本益比列完全沒有作用，而且文字會誤導
- **修法建議**：把 current_data 的 valuation（目前參考值）接進比較頁，或者把個股的空值文案改為「未接入」並與 ETF 的「不適用」分開
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/research_context.py:684-698（個股分支 fund_status = "unavailable"，pe、pb 等欄位都不填）
  - frontend/src/pages/TaiwanStockCompare.tsx:136（fmtNum：null 顯示為 'N/A（不適用）'）
  - frontend/src/pages/TaiwanStockCompare.tsx:378-383（非 ETF 就顯示 fmtNum(pe)）
  - backend/app/taiwan/ai_research.py:259-275（status 不是 available 時把 fundamentals_context 加入 missing_items）
  - research_context.py:40 引入 TaiwanFundamentalStore 與 latest_as_of，但沒有使用
- **線上證據**：
  - https://histock.tw/stock/2454（本益比 85.63，個股本益比是有定義、可取得的值）
  - https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/staticFiles/swagger_decoded.json（官方 BWIBBU_ALL 提供 PEratio）
- **本地計算證據**：
  - data/taiwan/fundamentals 目錄不存在，TaiwanFundamentalStore 從未被寫入
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：research_context 對個股固定回傳 fundamentals_context status=unavailable，數值全為 null。這是 phase 6G 刻意的 fail-closed 決策（docs/taiwan-fundamentals-availability-phase-6g.md:7-15），不是漏接；AI 研究報告因此把 fundamentals_context 列為缺漏，是正確揭露，不是缺陷。缺陷在比較頁 TaiwanStockCompare.tsx:383：它用 fmtNum(null) 把 unavailable 顯示成「N/A（不適用）」，和 ETF 的「ETF 不適用」並列，把「沒有時點資料」誤說成「個股不適用本益比」。以 2026-09-23 重現：自選 5 檔個股全部顯示「N/A（不適用）」，其中 4 檔有官方本益比（2454=85.63、3034=19.02、2379=27.59、6196=24.86），被誤標為不適用；3545 的官方值為 0.00（推測是虧損、不計算本益比），顯示不適用碰巧沒錯。建議只改文案，改成「無時點資料／未接入」並與 ETF 的「不適用」分開；不要直接把目前的 BWIBBU 值接進支援歷史日期的比較頁，以免造成前視偏差。另外，前後端測試假資料假設個股 fundamentals 為 available，掩蓋了這個文案問題。
    - 理由：我照數據重現的角度驗證。我寫了唯讀腳本（socket、security master 線上載入、各 store 的 write_batch 全部封鎖），直接呼叫真實的 TaiwanStockResearchContextService.get_research_context，對自選 9 檔、日期 2026-09-23 取得 fundamentals_context，再比照前端 fmtNum 的邏輯算出比較頁會顯示的文字，最後與官方參考資料 MI_INDEX 9/23 的本益比並列。跑完後 git status 沒有變化，data 目錄最近 10 分鐘內沒有新寫入的檔案。

重現結果（5 檔個股）：2454、3034、2379、6196、3545 都是 status=unavailable，pe、pb、dividend_yield、latest_eps 全為 null，比較頁都顯示「N/A（不適用）」。4 檔 ETF 都是 not_applicable，顯示「ETF 不適用」。官方 MI_INDEX 9/23 的本益比：2454 為 85.63（與 finding 引用的 HiStock 數值 85.63 一致）、3034 為 19.02、2379 為 27.59、6196 為 24.86、3545 為 0.00。所以 finding 的核心成立：4 檔有官方本益比的個股，被顯示成「不適用」，而且和 ETF 的「ETF 不適用」並列。所有引用的行號都經我實查正確：research_context.py:40 的 import 確實沒被使用；TaiwanFundamentalStore 在整個 repo 裡只有測試會建立實例；data/taiwan/fundamentals 目錄不存在。

要修正的三點：
(1) 個股固定為 unavailable 不是「沒接上」的疏漏，而是刻意的 fail-closed 設計。docs/taiwan-fundamentals-availability-phase-6g.md:7-15 明寫，BWIBBU_ALL 等資料源沒有首次發布時間，所以拒絕接入時點（point-in-time）脈絡，並且寫明「This is an intentional fail-closed result, not a missing fallback」。因此 AI 研究報告把 fundamentals_context 列為缺漏（ai_research.py:273-275），是誠實揭露資料缺口的正確行為，不是缺陷，不應算進問題裡。真正的缺陷只有前端文案把 unavailable 說成「不適用」。
(2) 3545 的官方本益比是 0.00。我推測（未上網核實）這代表近四季虧損、不計算本益比，那麼對它顯示「不適用」碰巧沒錯。所以「5 檔都被誤導」應改為「4 檔有官方本益比卻顯示不適用」。修正時，若改接 BWIBBU，也要把 0 或空值對應成「不適用／虧損」，不能直接顯示 0.00。
(3) 修法建議的第一個選項有風險。比較頁支援歷史 comparison_date，如果把 current_data 的目前估值值直接接進去，會在歷史比較中造成前視偏差（look-ahead），也違反 phase 6G 的決策。比較安全的做法是只改文案，把「未接入／無時點資料」與 ETF 的「不適用」分開。

新面向：前後端測試的假資料都假設個股的基本面是 available（backend/tests/test_taiwan_stock_comparison.py:346-348 用 stub 斷言 2330 的 pe 不是 None；frontend/src/pages/TaiwanStockCompare.test.tsx:63 預設 status 'available'、pe: 18），但正式環境的程式碼永遠不會產生這個狀態，所以測試掩蓋了這個文案問題。嚴重度維持 P3：只影響局部顯示與體驗，不牽涉交易結果。
    - 證據：
      - 重現腳本：AUDIT_SCRATCH/work/verify-fundpe/repro.py，執行指令 `cd backend && uv run python <repro.py>`（網路與寫入全部封鎖）
      - 重現輸出（2026-09-23）：2454 status=unavailable, pe=null, 顯示『N/A（不適用）』，官方 PE 85.63；3034 同樣 unavailable／null／N/A，官方 19.02；2379 官方 27.59；6196 官方 24.86；3545 官方 0.0；0050、00878、00885、00893 為 not_applicable，顯示『ETF 不適用』
      - 官方參考資料：reference/csv/twse_mi_index_20260923_quotes.csv 的 pe_ratio 欄位；raw/twse_mi_index_20260923.json 的『本益比』欄位值相同（2454=85.63、3034=19.02、2379=27.59、6196=24.86、3545=0.00）
      - backend/app/taiwan/research_context.py:684-697：個股分支 fund_status="unavailable"，只設定 status 與 meta，數值欄位全部不填
      - backend/app/taiwan/research_context.py:40：FundamentalRecord、TaiwanFundamentalStore、latest_as_of 被 import，但在檔內沒有其他地方使用（grep 只命中這一行）
      - frontend/src/pages/TaiwanStockCompare.tsx:136：fmtNum 遇到 null 會回傳 'N/A（不適用）'；:378-383：只有 not_applicable 才顯示『ETF 不適用』，其他情況都走 fmtNum(pe)
      - backend/app/taiwan/ai_research.py:259-275：status 不是 available 時，把 fundamentals_context 加入 missing_items
      - docs/taiwan-fundamentals-availability-phase-6g.md:7-15、24：刻意採 fail-closed，BWIBBU_ALL 以『No first-publication timestamp』為由被拒絕接入時點脈絡（'intentional fail-closed result, not a missing fallback'）
      - ls data/taiwan：沒有 fundamentals 目錄；repo grep 顯示 TaiwanFundamentalStore 只在 backend/tests/test_taiwan_fundamentals.py:46 被建立實例
      - backend/tests/test_taiwan_stock_comparison.py:346-348 與 frontend/src/pages/TaiwanStockCompare.test.tsx:63：測試假資料假設個股 fundamentals 為 available、pe 不是 null，正式環境不會出現這個狀態
      - backend/app/taiwan/fundamentals.py:231-243 與 frontend\src\components\taiwan\TaiwanReferenceData.tsx:164-169：目前估值（BWIBBU_ALL PEratio）只在參考資料頁顯示為『估值參考』
      - 唯讀檢查：執行前後 git status --short 相同；data 目錄最近 10 分鐘內沒有新修改的檔案

### [P3 → P3] 官方基本面沒有快取：每開一次個股頁就重新下載 10 份全市場資料，ETF 也要下載 2 次（部分成立）

- **主張**：hybrid_provider 每次呼叫都新建一個 TaiwanOfficialFundamentals（連同新的 httpx.Client，沒有關閉）。一次個股頁請求會依序下載 t187ap05_L、6 份 t187ap06_L_*、t187ap07_L_*、BWIBBU_ALL、t187ap03_L，共 10 份全市場 JSON；ETF 的 profile 與 snapshot 也各自重新下載一次 t187ap47_L。這些資料一天只更新一次，前端只有 5 分鐘的 staleTime，按「更新」又會全部重抓
- **正確的定義或數值**：依資料集與出表日期做行程內快取（例如一天或數小時），同一資料集跨個股共用，並且找到符合的類別後就停止
- **app 實際行為**：每次開頁都下載 10 份全市場資料
- **對使用者的影響**：詳細頁的參考資料載入較慢；對官方 openapi 的請求量偏高，有被限流的風險（本次稽核規定本身也警告連官方可能被封 IP）
- **修法建議**：在 TaiwanOfficialFundamentals 與 TaiwanOfficialETFData 的 _rows 加上以 (host, path) 為鍵、帶 TTL 的快取；hybrid_provider 改為共用單一 provider 或 client
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/hybrid_provider.py:58-60,83-85（每次呼叫都 new provider）
  - backend/app/taiwan/fundamentals.py:176-177,335-343（沒有快取的 client.get）
  - backend/app/taiwan/etf_data.py:177-178,225-238
  - frontend/src/pages/TaiwanStockDetail.tsx:116-121（staleTime 5 分鐘）
  - CONTRIBUTING.md:163-177（第 6 節：緩存與性能）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 3 份 backend.log 合計：openapi.twse.com.tw 的基本面端點 t187ap05_L 為 118 次、t187ap06_L_* 各 118 次、BWIBBU_ALL 為 118 次、t187ap03_L 為 120 次，約 118 次開頁、超過 1,180 次全市場下載；每日次數：09-17 為 318、09-18 為 267、09-22 為 360、09-23 為 193
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：官方基本面與 ETF 參考資料完全沒有快取。每次請求 /api/taiwan/data/{symbol} 都會新建 TaiwanCurrentDataService 與 TaiwanHybridProvider。個股的 4 個 dataset 各 new 一個 TaiwanOfficialFundamentals，每個都帶一個沒有明確關閉的 httpx.Client，所以一頁有 4 個 client。每次個股請求依序下載 10 份 TWSE 全市場 JSON：t187ap05_L、t187ap06_L_{ci,basi,bd,fh,ins,mim}、t187ap07_L_<命中類別>、BWIBBU_ALL、t187ap03_L。ETF 的 profile 與 snapshot 各重新下載一次 t187ap47_L（約 341 KB），一頁共 2 次。

3 份 backend.log 合計：t187ap05_L、6 個 t187ap06_L_*、BWIBBU_ALL 各 118 次，t187ap07_L 為 ci 117＋ins 1。t187ap03_L 為 120 次，其中 2 次來自排程的 share_capital_store，不是開頁造成的。個股端點合計 1182 次，相當於 118 次個股 current-data 請求；log 沒有 access log，無法區分其中哪些是開頁、手動重新整理或 refetch。t187ap47_L 為 22 次，即 11 次 ETF 請求。每日次數依 log 本地時間為 09-17：340、09-18：270、09-22：340、09-23：192、09-24：40；finding 原本寫的 318／267／360／193 無法重現。

每次請求的 10 筆下載耗時中位數約 4.9 秒，最長 48.5 秒。前端 staleTime 為 5 分鐘，「更新」按鈕會強制重抓。

修法：在 _rows 與 ETF 的 _row 加上以 URL 為鍵、帶 TTL 的行程內快取，並共用 client。t187ap03_L 也可以改讀已經由排程寫入的 share_capital.parquet。財報類別的查找必須保留「6 類中剛好命中 1 類」的檢查，不能在第一個命中時就停止；如果要減少下載量，應該靠快取，不是提早結束。
    - 理由：核心敘述全部重現。程式碼方面：每次 /api/taiwan/data/{symbol} 請求都會新建 TaiwanCurrentDataService 與 TaiwanHybridProvider。個股的 4 個 dataset 各自 new 一個 TaiwanOfficialFundamentals，每個都帶一個沒有關閉的 httpx.Client；_rows 每次都直接 client.get，路徑上完全沒有快取。所以一個 TWSE 個股頁固定下載 10 份全市場 JSON：t187ap05_L、6 份 t187ap06_L_*、t187ap07_L_<命中類別>、BWIBBU_ALL、t187ap03_L。ETF 的 profile 與 snapshot 則各下載 1 次 t187ap47_L。

我自己寫腳本重算 3 份 backend.log，各端點次數與 finding 完全一致：t187ap05_L 118、t187ap06_L_* 各 118、BWIBBU_ALL 118、t187ap03_L 120，t187ap07_L 為 ci 117＋ins 1。7 個個股端點合計 1182 次，與 finding 的「超過 1,180 次」相符。t187ap03_L 多出的 2 次，查 log 上下文是 16:30 與 21:40 排程 daily_update 裡的 share_capital_store，不是開頁造成的。以 30 秒間隔切段，有 75 段剛好 10 筆，順序與程式碼一致；另有 20／30／50／60／80 筆的段落，拆開看是多個 10 筆區塊依序相接。t187ap47_L 共 22 次，剛好是 11 組，每組兩次相隔 0.6～4 秒，符合 ETF 開一次頁下載 2 次。

前端 staleTime 是 5 分鐘，「更新」按鈕會強制 refetch currentDataQuery，這兩點都屬實。

無法重現的是 finding 的每日次數。finding 寫 09-17 為 318、09-18 為 267、09-22 為 360、09-23 為 193，合計 1138，與它自己的總數 1182 對不上。我依 log 本地時間（+08:00）分日，算出 340／270／340／192，另外 09-24 還有 40 次；改用 UTC 分日得到 360／250／340／232，也對不上。因此判為部分確認。

另有兩處敘述需要修正：
- 「約 118 次開頁」：log 沒有 access log，只能確認是 118 次個股 current-data 請求，其中可能包含重新整理、staleTime 過期後的 refetch 或腳本呼叫，無法全部歸為使用者開頁。
- 修法建議「找到符合的類別後就停止」：這會改變 fundamentals.py:207 的「剛好一個類別命中」歧義檢查，不能直接照做。

「一天只更新一次」：本地參考資料的 t187ap03_L（1095 列）與 t187ap47_L（271 列）都只有單一出表日期 1150922，是日級快照，支持這個說法。其他 8 個端點的更新頻率沒有上網核實，因為 WebSearch 額度已用完。

嚴重度維持 P3：屬於效能與請求量問題，不影響數值正確性。另外量到每次開頁的 10 筆下載耗時中位數 4.94 秒、最長 48.5 秒，可以佐證「載入較慢」。
    - 證據：
      - backend/app/taiwan/providers/hybrid_provider.py:58-60：每次 get_fundamentals 都執行 provider = TaiwanOfficialFundamentals()；:83-85 的 get_etf_data 每次也都 new TaiwanOfficialETFData()
      - backend/app/taiwan/fundamentals.py:176-177：self.client = client or httpx.Client(timeout=30.0)；:335-343 的 _rows 直接 self.client.get(url)，沒有快取。grep close/lru_cache/_cache 在 fundamentals.py、etf_data.py、current_data.py、hybrid_provider.py 都沒有命中（hybrid_provider 裡的 close 是股價欄位名，不是資源釋放）
      - backend/app/taiwan/fundamentals.py:201-210：逐一抓 6 個 STATEMENT_CATEGORIES，要求 len(matches)==1，再抓 1 份 t187ap07；所以不能照 finding 建議「命中後就停止」，否則會移除歧義檢查
      - backend/app/taiwan/etf_data.py:177-178,181,201,225-231：profile() 與 snapshot() 各自呼叫 _row()，_row() 每次都 client.get(TWSE_ETF_PRODUCTS_URL)
      - backend/app/taiwan/current_data.py:175-181,204-213,272-273：個股 4 個 dataset、ETF 2 個 dataset；get_taiwan_current_data_service() 每次請求都 new TaiwanCurrentDataService() 與 TaiwanHybridProvider()，由 backend/app/api/taiwan.py:255-265 的 /data/{symbol} 路由呼叫
      - frontend/src/pages/TaiwanStockDetail.tsx:116-121 設定 staleTime: 5 * 60_000；:318-323 的「手動重新整理」按鈕呼叫 currentDataQuery.refetch()
      - 本地計算：cd backend && uv run python scratchpad\audit\work\verify-g2cache\count_log.py（輸出在 count_log.out）。各端點合計為 BWIBBU_ALL 118、t187ap03_L 120、t187ap05_L 118、t187ap06_L_basi/bd/ci/fh/ins/mim 各 118、t187ap07_L_ci 117、t187ap07_L_ins 1、t187ap47_L 22；個股端點合計 1182；所有請求都是 HTTP 200；3 個檔案之間沒有重複列
      - 本地計算，每日次數（log 本地時間，不含 t187ap47_L）：09-17 為 340、09-18 為 270、09-22 為 340、09-23 為 192、09-24 為 40，合計 1182。finding 寫 318／267／360／193（合計 1138），無法重現。改以 UTC 分日得到 09-17 為 360、09-18 為 250、09-22 為 340、09-23 為 232，同樣不符
      - 本地計算，以 30 秒間隔切段：75 段剛好 10 筆，順序為 t187ap05_L→t187ap06_L_ci/basi/bd/fh/ins/mim→t187ap07_L_ci→BWIBBU_ALL→t187ap03_L；每段耗時最短 1.47 秒、中位數 4.94 秒、最長 48.5 秒。例如 backend.log:1923-1957（2026-09-22 15:43:10 起）可以看到連續的 10 筆區塊
      - data/backend.log:37259-37260 與 :45471-45475：多出的 2 次 t187ap03_L 出自排程 _scheduled_taiwan_update 裡的 share_capital_store（log 寫 'TWSE returned 1095 rows'），不是開頁造成的
      - 本地計算：t187ap47_L 的 22 次恰好是 11 組配對，每組相隔 0.6～4.3 秒，例如 2026-09-17 11:16:30.530 與 11:16:31.716、2026-09-23 18:38:17.587 與 18:38:18.355，符合 ETF profile 與 snapshot 各下載 1 次
      - 官方參考資料 reference\raw\twse_openapi_t187ap03_L.json 為 1,328,628 bytes、1095 列，出表日期全部是 1150922；twse_openapi_t187ap47_L.json 為 341,439 bytes、271 列，出表日期全部是 1150922。兩者都是日級快照
      - 線上核實：WebSearch 回報額度已用完（200/200），沒有取得官方更新頻率的線上來源；除了上面兩個資料集，其餘端點「一天只更新一次」的說法沒有驗證

## 確認正確的項目

- **月營收金額換算：當月、上月、去年同月、累計營收（千元乘以 1000）**：用 Yahoo 2454 在 2026/08 的數值手工組一列 t187ap05_L，透過 httpx.MockTransport 餵給 TaiwanOfficialFundamentals.monthly_revenue（不連網）。結果 revenue=64,183,361,000、previous_month=48,474,930,000、previous_year_month=44,546,643,000、cumulative=413,991,412,000，與獨立計算（千元 × 1000）完全一致。前端 money() 以 node 重現，顯示為「641.83 億元」與「4,139.91 億元」。程式碼：backend/app/taiwan/fundamentals.py:43-45,184-193；frontend/src/components/taiwan/TaiwanReferenceData.tsx:63-70,145-152
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/revenue（單位「仟元」；2026/08 當月營收 64,183,361、去年同月 44,546,643、累計 413,991,412）
  - 來源：https://raw.githubusercontent.com/PatrickSUDO/fadacai-portfolio/main/tools/fetch_leading.py（第三方讀取 t187ap05_L 後，欄位命名為 rev_month_ntd_k，也就是千元）
  - 來源：AUDIT_SCRATCH/work/G02-gap/v2.out
- **月增率與年增率的單位（官方給的是 % 數值，前端 percent() 直接加上 %）**：官方欄位「營業收入-上月比較增減(%)」與「營業收入-去年同月增減(%)」由 parse_number 原值帶入（fundamentals.py:188-189）。以 Yahoo 數值獨立重算：64,183,361/48,474,930-1=32.41%，64,183,361/44,546,643-1=44.08%，與 Yahoo 顯示的 32.41%、44.08% 一致。app 取得 mom=32.41、yoy=44.08，前端 percent() 顯示為「32.41%」「44.08%」，沒有重複乘 100（TaiwanReferenceData.tsx:72-75,149-150）
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/revenue（2026/08 月增率 32.41%、年增率 44.08%）
  - 來源：本地 node 重現：AUDIT_SCRATCH/work/G02-gap/fmt.js
- **roc_month 民國年月換算**：讀程式碼 fundamentals.py:61-68：年 = 去掉末兩碼後的數字 + 1911，月 = 末兩碼，並檢查月份在 1～12。實測 11508→2026-08、11501→2026-01、11512→2026-12、10001→2011-01，11513 會拋出 ValueError。第三方 fadacai-portfolio 的 _roc_month 用同樣的算法（int(digits[:-2]) + 1911）
  - 來源：https://raw.githubusercontent.com/PatrickSUDO/fadacai-portfolio/main/tools/fetch_leading.py（_roc_month：year, month = int(digits[:-2]) + 1911, int(digits[-2:])）
  - 來源：v2.out
- **財報 cumulative_eps 是年初至今累計，不是單季，也不是近四季；前端標示「累計 EPS」正確**：第三方測試明寫 TWSE t187ap06_L 的 Q2 是 YTD（months_covered 6）、Q4 是全年，而且「不可直接推導單季、TTM 或估值」。程式碼取「基本每股盈餘（元）」（fundamentals.py:223），前端標示「累計 EPS」（TaiwanReferenceData.tsx:160）。Yahoo 的 2454 單季 EPS 為 2026Q1 15.17、Q2 15.28，推算上半年累計約 30.45；近四季合計 60.69，5185/60.69≈85.4，接近 HiStock 的本益比 85.63，所以 PE 用的是近四季口徑，與累計 EPS 的口徑不同，兩者標籤各自正確
  - 來源：https://raw.githubusercontent.com/lulu930128/open-market-intelligence/main/backend/tests/test_financial_metric_semantics.py（Q2 標為 ytd、months_covered 6；「不可直接推導單季、TTM 或估值」）
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/eps（單季 EPS：2026Q2 15.28、Q1 15.17、2025Q4 14.4、Q3 15.84）
  - 來源：https://histock.tw/stock/2454（本益比 85.63、收盤 5,185）
- **財報 period_end 由季別推算、period_start 為年初**：用手工組的 ci 資料列逐季實測：Q1→2026-03-31、Q2→2026-06-30、Q3→2026-09-30、Q4→2026-12-31，period_start=2026-01-01（fundamentals.py:211-216,229）
  - 來源：v2.out（quarter 1..4 皆 OK）
- **accounting_category 比對到多筆或 0 筆就放棄**：fundamentals.py:201-208 逐一抓 6 個類別，符合的筆數不等於 1 就拋出 LookupError，由 current_data.py:262-269 轉成 section error，屬於 fail-closed，不會拿錯類別的數字。TWSE swagger 各類別（basi、fh…）的欄位各自獨立。另外，金融業（basi）沒有營業收入與營業利益欄位，所以這兩項顯示 --，淨利與 EPS 正常（實測 basi 列）
  - 來源：https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/staticFiles/swagger_decoded.json（t187ap06_L_basi 的欄位：利息淨收益、利息以外淨損益…本期稅後淨利（淨損）、基本每股盈餘（元））
  - 來源：v2.out（basi statement revenue None、net_income 40000）
- **TWSE 估值 BWIBBU_ALL 的欄位名稱（Date、Code、PEratio、PBratio、DividendYield），殖利率單位是 %**：TWSE swagger 的回應 schema 為 Date（日期）、Code、Name、PEratio（本益比）、DividendYield（殖利率(%)）、PBratio（股價淨值比），與 fundamentals.py:236-241 一致。以 HiStock 的 2454 數值組列實測，得到 pe=85.63、pb=19.51、dividend_yield=1.03，前端顯示「1.03%」。PEratio 為空字串（虧損）時得到 None，前端顯示 --
  - 來源：https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/staticFiles/swagger_decoded.json（BWIBBU_ALL：Date、Code、Name、PEratio、DividendYield(殖利率(%))、PBratio）
  - 來源：https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/tests/tool_field_dependencies.py（"/exchangeReport/BWIBBU_ALL": ["Code", "Date", "DividendYield", "Name", "PBratio", "PEratio"]）
  - 來源：v2.out
- **TPEx 估值 tpex_mainboard_peratio_analysis 的欄位名稱（SecuritiesCompanyCode、PriceEarningRatio、PriceBookRatio、YieldRatio），殖利率單位是 %**：第三方對官方 API 做的 e2e 測試要求這些欄位存在；第三方工具把 YieldRatio 直接加 % 顯示。fundamentals.py:239-241 的後備欄位名稱一致。實測 TPEx 列，N/A 會得到 None
  - 來源：https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/tests/e2e/test_otc_api.py（tpex_mainboard_peratio_analysis 必要欄位：SecuritiesCompanyCode、CompanyName、PriceEarningRatio、PriceBookRatio，加上 YieldRatio 或 DividendYield）
  - 來源：https://raw.githubusercontent.com/twjackysu/TWSEMCPServer/main/tools/otc/peratio.py（f"...殖利率: {dy}%..."）
- **股本紀錄：issued_shares、capital_twd、par_value 的解析（TWSE 與 TPEx）**：把已下載的 twse_openapi_t187ap03_L.json、tpex_openapi_mopsfin_t187ap03_O.json 透過 MockTransport 餵給 share_capital。2454、3034、2379、6196、3545、2330、6488、8069 全部與獨立解析一致，例如 2454 為 1,604,952,613 股、16,049,526,130 元、面額 10。並以恆等式核對：(已發行+私募+特別股)×面額 等於實收資本額（2454 等 5 檔成立）。第三方 HiStock 的 6196 股本 2,203,295,290、股數 220,329,529 與官方完全相同；2379 為「NT$5.15 billion、515 million shares」，也一致
  - 來源：AUDIT_SCRATCH/work/G02-gap/v1.out
  - 來源：https://histock.tw/stock/6196
  - 來源：https://histock.tw/stock/2379
- **已發行普通股數包含私募股（官方原值照搬）**：掃描 1,095 筆 TWSE 資料：私募股數大於 0 的 103 筆，全部符合 (已發行+特別股)×面額=實收資本額，表示官方的「已發行普通股數」已經含私募股。app 照搬官方欄位並標示「已發行普通股數」，不算錯
  - 來源：本地計算（見 G02-gap 目錄下的指令輸出：rows with private>0: EXCLUDES 0 / INCLUDES 103）
- **ETF profile（名稱、追蹤指數、基金類型、成立日期、上市日期）與 snapshot（發行單位數、資料日期）**：把 twse_openapi_t187ap47_L.json 餵給 TaiwanOfficialETFData。0050 的成立與上市日期由 0920625、0920630 換算為 2003-06-25、2003-06-30；00878、00885、00893 同樣正確。發行單位數：0050=22,051,000,000、00878=18,787,290,000、00885=591,238,000、00893=355,884,000，與官方原值一致。全部 271 檔的 profile 與 snapshot 都沒有解析失敗，成立日期全為 7 碼，單位數都沒有小數
  - 來源：v1.out
  - 來源：backend/app/taiwan/etf_data.py:180-223
- **ETF snapshot 的 nav、premium_discount、aum、outstanding_units 固定為 None，畫面處理正確**：etf_data.py:205-223 寫死為 None。TaiwanReferenceData.tsx:202-212 對應欄位顯示 --，並附註「歷史 NAV、資產規模與配息目前尚未納入…缺值不代表為 0」。premium_discount 沒有任何畫面顯示；calculate_premium_discount 在 backend/app 與 frontend/src 都沒有被呼叫（grep 只在 etf_data.py 本身出現），不會顯示錯的折溢價
  - 來源：grep calculate_premium_discount|premium_discount 的結果為 0 個呼叫點（僅定義處）
- **ETF 的 leverage_multiplier、inverse 與研究脈絡**：TaiwanETFProfile 的 leverage_multiplier、distribution_policy、issuer 永遠是 None（etf_data.py:184-198），但參考資料卡沒有顯示這些欄位。研究脈絡改用證券主檔的 leverage_multiplier（research_context.py 的 ETF 分支）。實查主檔：0050、00878、00885、00893 為 1.0，00631L 與 00675L（正2）為 2.0，00632R 與 00673R（反1）為 -1.0，與基金名稱相符
  - 來源：data/taiwan/security_master.parquet（唯讀查詢）
- **參考資料卡的格式化函式（money、percent、number）與各列標籤**：用 node 逐字重現 TaiwanReferenceData.tsx:39-75：PE 85.63 顯示「85.63」；股數 1,604,952,613 顯示「1,604,952,613」；實收資本額 16,049,526,130 顯示「160.5 億元」；面額 10 顯示「10 元」；EPS 33.8 顯示「33.8」。估值卡的交易日期取 period_end，也就是 BWIBBU 的 Date
  - 來源：fmt.js 的執行輸出
- **5 檔自選股的收盤價，第三方與官方一致**：HiStock 顯示 2454=5185、3034=545、2379=761、6196=549、3545=51.3（2026/09/23），與參考資料 twse_stock_day 在 9/23 的收盤價完全相同
  - 來源：https://histock.tw/stock/2454
  - 來源：https://histock.tw/stock/3034
  - 來源：https://histock.tw/stock/2379
  - 來源：https://histock.tw/stock/6196
  - 來源：https://histock.tw/stock/3545
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
- **/api/financials/metrics 在純台股部署的實際回應（回 200 加上空資料，不是 403）**：在程序內直接呼叫 get_metrics（唯讀）。detect_capabilities() 固定開啟 Cap.FINANCIAL（policy.py:25），data/financials/metrics 是空的，所以 2454.TWSE、3034.TWSE 與不帶 symbol 都回 {'data': []}。問題本身列在 findings
  - 來源：backend/app/api/financials.py:38-49
  - 來源：backend/app/tickflow/policy.py:10-28

## 無法驗證的項目

- **app 對 5 檔自選股實際顯示的 PE、PB、殖利率、最新月營收年增率、累計 EPS 數值（三方數值比對）**：這些值只能由 TaiwanOfficialFundamentals 即時抓 openapi.twse.com.tw 取得，規定不得連官方，本地 data 也沒有任何快取（data/taiwan/fundamentals 不存在）。目前只取得第三方數值：HiStock 2454 PE 85.63／PB 19.51／殖利率 1.03%／Q2 EPS 15.28；3034 為 19.02／4.81／4.22%／10.4；2379 為 27.59／7.78／3.29%／7.42；6196 為 24.86／6.77／1.17%／8.17；3545 PE 顯示「0」、PB 1.25、殖利率 1.58%、Q2 EPS 0.62。Yahoo 2454 在 2026/08 的營收年增率 44.08%。3545 的股本在 HiStock 是 2,211,512,150 元（221,151,215 股），官方 2026-09-22 為 2,224,113,500 元（222,411,350 股），推測是 HiStock 資料較舊，app 用的是官方數字
- **TPEx 月營收 mopsfin_t187ap05_O 與財報 mopsfin_t187ap06_O_*、07_O_* 的欄位名稱**：找不到 TPEx swagger 的鏡像，Sourcegraph 只找到端點 URL，沒有欄位。風險：同系列的 mopsfin_t187ap03_O 經本地實查是英文欄位（Date、SecuritiesCompanyCode、IssueShares…）。如果 05_O 也是英文欄位，fundamentals.py:185-192 只讀中文欄位，roc_month(None) 會拋錯，月營收整段變成 error（已用手工英文列實測得到 ValueError）；財報則會變成各值全部 None（fundamentals.py:211-212 只有年度與季別有 Year／Season 後備名稱）。自選 5 檔都是上市股，不受影響
- **TPEx tpex_mainboard_peratio_analysis 是否有 Date 欄位**：第三方 e2e 測試的必要欄位沒有列 Date。如果沒有這個欄位，valuation 會拋出 ValueError: invalid Taiwan date: 'None'，整段變成 error（已實測）
- **虧損公司在 BWIBBU_ALL 與 TPEx 的本益比佔位值（空字串、N/A、0.00、全形－）**：沒有取得官方樣本。HiStock 對 3545 的本益比顯示「0」。如果官方給的是 "0.00"，app 會顯示「本益比 0」而不是 --；如果給的是全形「－」，parse_number 會拋出 malformed number，整個估值段變成 error（已實測）
- **t187ap05_L 的完整欄位名稱（含 (%) 後綴）**：swagger 鏡像的內容被截斷，沒有取到 t187ap05_L 的 schema。只有間接證據：第三方用子字串比對「當月營收」「去年同月增減」「年月」，與 app 使用的完整鍵名相容；另外 log 顯示 app 反覆成功取得 t187ap05_L（HTTP 200），但回應內容無從得知
- **t187ap06／t187ap07 財報金額的單位是千元**：沒有取得官方或第三方的單位說明或樣本數值；依 MOPS 彙總報表慣例應為千元（推測）
- **t187ap47_L 的「發行單位數/轉換數」是目前的流通單位數，還是發行或轉換時的單位數**：網路請求已用完，沒有第三方單位數可比對。畫面標示「發行單位數」，與官方欄位名稱一致，並另列「流通單位數」為 --
- **calculate_premium_discount 的線上權威公式**：zh.wikipedia 的 ETF 條目與 en.wikipedia 的 Closed-end fund 條目都沒有公式，investopedia 無法抓取。程式公式為 market_price/nav-1，代數上等於 (價-淨值)/淨值；而且這個函式沒有被任何路徑呼叫，不影響畫面
- **TPEx 財報抓取在 log 中 3 次中止的實際例外類型**：current_data.py:262-269 吞掉例外，只回傳 reason，沒有寫 log，只能從請求序列推斷（見 findings）
