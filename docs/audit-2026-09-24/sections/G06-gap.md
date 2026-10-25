# G06-gap｜G6-industry-classification

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 3 | 0 | 0 | 0 | 0 | 9 | 5 |

## 稽核者摘要

結論：TAIWAN_INDUSTRY_CODE_TO_NAME 的 34 個代碼與名稱，和證交所、櫃買中心現行正式目錄在用的代碼完全吻合。9/22 的上市目錄用了 32 種代碼，9/23 的上櫃目錄用了 28 種；除了 91 以外全部在對照表內，對照表裡也沒有「沒人使用」的代碼。名稱和 MI_INDEX 9/23 官方類股指數的 32 類一一對應，其中包括 2023 年新增的綠能環保、數位雲端、運動休閒、居家生活，以及觀光餐旅（觀光事業更名後的名稱）。我在線上抽查了 12 個代碼（Yahoo 奇摩、HiStock），全部一致。Security Master 的 1,986 檔股票，產業與官方目錄比對 0 筆不符。自選 5 檔的產業都正確：2454、3034、2379、3545 是半導體業（官方代碼 24），6196 是其他電子業（代碼 31），與 Yahoo 和 HiStock 相符。
ETF 部分，271 檔上市 ETF 的 instrument_type、underlying_scope 與官方 t187ap47_L 一致 271/271；51 檔槓桿／反向 ETF 加上 00716R 的 leverage_multiplier 全部正確。自選 4 檔 ETF 的 0050、00878 是 domestic_equity，00885、00893 是 foreign_equity，都正確。
發現 3 個問題：
(1) P2：官方代碼 91（臺灣存託憑證，10 檔 -DR）在 OpenAPI 載入路徑被當成一般股票，並落入「未知產業（91）」fallback。產業情報因此把它列成第 35 個產業，data_quality 仍回報 classification_coverage_pct=100.0、unclassified=0。9/23 還拿這 10 檔互不相關的外國公司當同業，對 910861 神州-DR 實際觸發 RELATIVE_STRENGTH_OUTLIER LEADER（observed 11.76%，baseline 0.37%）。
(2) P3：11 檔上市 ETF 的 etf_category 與官方基金類型矛盾：3 檔主動式債券 ETF 與 8 檔期貨信託 ETF 都被標成 foreign_equity，其中 00716R 其實是反向型。這個標籤會顯示在個股頁徽章與 AI 研究脈絡。
(3) P3：Security Master 的 listing_date 混用三種格式（民國 7 碼、YYYYMMDD、YYYY/MM/DD），與模型宣告的 YYYY-MM-DD 契約不符。
已知且不重複回報的項目：7812、7856 新掛牌未進 master（F9）；55 檔 classification_source 為 null 的 ETF 導致漲跌停與稅率被拒算（A05 的 twse_etf_class.csv 已涵蓋）；上櫃 ETF 的 CEOGEU 問題（A05）。
稽核過程：只讀取資料，沒有寫入 repo 或 data。git status 與起始時相同；data 目錄下沒有任何檔案比我的第一支腳本新。網路請求共 25 次：24 次 WebFetch，另有 1 次 WebSearch 因 session 搜尋額度用完而未執行。

## 問題清單

### [P2 → P2] 代碼 91（臺灣存託憑證）被當成一般股票，形成『未知產業（91）』偽產業、被算進已分類，還觸發錯誤的相對強弱訊號（確認成立）

- **主張**：industry_classification.py 的 docstring 把 91 定義為『商品類型標記，不是產業』，預期它會落入『誠實的 unknown fallback』。實際上 OpenAPI 載入路徑把 10 檔 TDR 全部建成 instrument_type='stock'、underlying_scope='domestic'；下游的產業情報、異常診斷與研究脈絡又把字串『未知產業（91）』當成真正的產業。結果是：分類覆蓋率被高報為 100%，產業排行多出一個偽產業，而且拿 10 檔不同行業的外國公司互為同業，發出 RELATIVE_STRENGTH_OUTLIER 訊號。
- **正確的定義或數值**：TDR（代碼 91）應被視為獨立的證券類型『存託憑證』：要嘛不列入個股產業排行與大盤等權基準，要嘛以明確的『存託憑證』標籤呈現，但計為未分類。覆蓋率應為 1976/1986 = 99.5%，unclassified=10。RELATIVE_STRENGTH_OUTLIER 不應拿不同行業的外國公司籃子當同業基準。
- **app 實際行為**：coverage 100%、unclassified 0；產業表多出第 35 列『未知產業（91）』，前端顯示『35 大類股統計』；10 檔 TDR 被算進大盤等權 5 日與 20 日報酬基準；神州-DR 在 9/23 被標成『產業內領先』。
- **對使用者的影響**：自選的 5 檔股票（代碼 24、31）與 4 檔 ETF 都不受影響，監控規則 0 條。使用者在選股頁的產業情報表會看到一個不存在的產業；在異常診斷列表會看到 TDR 的相對強弱訊號，但比較對象不具意義；API 的 classification_coverage_pct 高報，前端沒有顯示這個欄位。
- **修法建議**：在 _official_company_directory 依代碼 91 或名稱結尾 -DR，把 TDR 標成獨立類型（或與 ISIN 路徑一致標成 unsupported）；同時讓 industry_intelligence 把『未知產業（…）』計為 UNCLASSIFIED，並在 industry 未知時跳過 RELATIVE_STRENGTH_OUTLIER。另外，verify-tdr 資料夾曾從股本或市值角度看過 TDR，本條只回報分類與產業面向，可能需要與那條合併處理。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/industry_classification.py:23-28 （docstring：91 是 instrument-type marker，應走 unknown fallback）
  - backend/app/taiwan/universe/industry_classification.py:92-93 （純數字的未知代碼回傳 f"未知產業（{value}）"，不是 None）
  - backend/app/taiwan/universe/adapters.py:413-420 （公司目錄每一列都寫成 instrument_type="stock"、underlying_scope="domestic"，沒有排除 TDR）
  - backend/app/taiwan/universe/adapters.py:259-263 （ISIN 路徑把 TDR 當成 unsupported，兩條載入路徑的行為不一致）
  - backend/app/taiwan/industry_intelligence.py:205-213 （只有 null 或空字串才標成 UNCLASSIFIED，『未知產業（91）』被算進 classified_count 與 coverage_pct）
  - backend/app/taiwan/industry_intelligence.py:367-371 （每個不同的字串都成為一個產業列）
  - backend/app/taiwan/abnormal_diagnostics.py:587-599 （以同一個 industry 字串的 average_return_5d 當基準，|diff|≥10% 就發出訊號）
  - backend/app/taiwan/research_context.py:651-653 （TDR 同樣取得『未知產業（91）』的產業脈絡）
  - frontend/src/pages/TaiwanScreener.tsx:656 （顯示『{n} 大類股統計』，現在是 35）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/9105.TW/profile ：泰金寶-DR 的『產業類別』是『存託憑證』
  - https://tw.stock.yahoo.com/class ：上市類股清單把『存託憑證』與 ETF、受益證券、ETN 並列為獨立類別，不屬於任何產業
- **本地計算證據**：
  - a1_codes.py 輸出：上市代碼 91 共 10 家，全部是 -DR（9103 美德醫療-DR、910322 康師傅-DR、9105 泰金寶-DR、910861 神州-DR、9110 越南控-DR、911608 明輝-DR、911622 泰聚亨-DR、911868 同方友友-DR、912000 晨訊科-DR、9136 巨騰-DR）；上櫃代碼 91 為 0 家。
  - a2_master.py 輸出：這 10 檔在 master 中是 instrument_type=stock、is_supported=true、underlying_scope=domestic、industry=未知產業（91）。
  - a5_intel.py（以 FakeStore 與 master 複本實跑 TaiwanIndustryIntelligenceService.get_snapshot(2026-09-23)）輸出：supported_stock_count=1986、classified_stock_count=1986、unclassified_stock_count=0、classification_coverage_pct=100.0、industry_count=35；『未知產業（91）』群組 supported 10、turnover 128,262,626、average_return_5d=0.003705、relative_strength_5d=-0.020605。
  - a5_intel.py 實跑 TaiwanAbnormalDiagnosticsService.get_diagnostics(2026-09-23, industry_filter='未知產業（91）')：910861.TWSE 神州-DR 發出 ('RELATIVE_STRENGTH_OUTLIER','LEADER', observed=11.76, baseline=0.37, 'moderate')。我獨立重算：close 9/16=4.04、9/23=4.53，r5=+12.13%，扣掉群組平均 +0.37% 後為 +11.76%，與 app 相同。
  - 指令：cd backend && uv run python AUDIT_SCRATCH/work/G06-gap/a5_intel.py
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：上市代碼 91 的 10 檔臺灣存託憑證（TDR）被當成一般股票處理。

- **載入時**：OpenAPI 公司目錄的載入路徑（adapters.py:413-420）把 TDR 建成 instrument_type=stock、underlying_scope=domestic，違反 docs/taiwan-market-contract-v1.md:7「v1 只有 stock 與 etf，TDR 延後」的契約。
- **分類時**：industry_classification.py 照設計回傳 fallback 字串「未知產業（91）」，但下游只把 null 或空字串當成未分類（industry_intelligence.py:205-213）。

結果如下：
1. 產業情報從 docstring 承諾的 34 個正式產業加 UNCLASSIFIED，變成 35 個產業、0 檔未分類，覆蓋率高報為 100%（應為 1976/1986≈99.5%，或另設「存託憑證」類別）。選股頁與儀表板都顯示「35 大類股」，並列出一個名為「未知產業（91）」的偽產業；官方與業界的正確標籤是「存託憑證」，屬於證券類型，不是產業。
2. 異常診斷拿 10 檔不同行業的外國公司當同業基準。2026-09-23 神州-DR 因此發出 RELATIVE_STRENGTH_OUTLIER/LEADER（11.76%，基準 0.37%），這個比較沒有意義。
3. research_context 也會帶出這個偽產業的脈絡。

對大盤等權基準的影響可以忽略（約 0.017 個百分點）。使用者的自選 9 檔不受影響。
    - 理由：這條 finding 成立，嚴重度 P2 恰當。

**程式碼與實際資料**
- 引用的行號都和現況相符。
- 實際的 security_master.parquet 裡，10 檔代碼 91 的 TDR 都被存成 instrument_type=stock、underlying_scope=domestic、industry='未知產業（91）'，source=TWSE_OPENAPI。
- 這份資料在 is_supported=true 且 listing_status=active 的條件下，只有 stock 與 etf 兩種類型，沒有其他證券類型。
- 官方參考資料 twse_openapi_t187ap03_L.json 的代碼 91 恰好是這 10 家 -DR，註冊地為 BM、KY、TH、SG；上櫃目錄沒有代碼 91。

**唯讀實跑結果**
我關掉網路，用真實 master 的複本與真實的日K parquet，重跑 TaiwanIndustryIntelligenceService.get_snapshot(2026-09-23)：
- supported=1986、classified=1986、unclassified=0、coverage=100.0、industry_count=35。
- TDR 群組的成交值為 128,262,626。我用官方 MI_INDEX 9/23 這 10 檔的成交金額逐一加總，也是 128,262,626，一致。
- get_diagnostics 對 910861 神州-DR 發出 RELATIVE_STRENGTH_OUTLIER／LEADER，observed=11.76、baseline=0.37。
- 我獨立重算：close 4.53／4.04−1=12.13%，群組平均 0.3705%，差距 11.76%，與 app 相同。

**正確定義（與 finding 不同的來源）**
- FinMind TaiwanStockInfo 把 9105 泰金寶-DR 與 910861 神州-DR 的 industry_category 都標成「存託憑證」。
- 中文維基百科的臺灣證券交易所條目，把臺灣存託憑證與 ETF、ETN、權證並列為上市股票以外的另一類商品。
- 專案自己的契約 docs/taiwan-market-contract-v1.md:7 規定 v1 的 security_type 只有 stock 與 etf，TDR 要等有經過驗證的官方 metadata 才納入。OpenAPI 載入路徑把 TDR 當成 stock，違反這條契約。
- industry_intelligence.py:5 的 docstring 寫明「34 canonical industries + UNCLASSIFIED tracking」，實際上卻是 35 個產業、0 檔未分類。

**需要修正的描述**
- industry_classification.py 本身照 docstring 運作：91 確實落入 f"未知產業（{value}）" 這個 fallback。這個 docstring 沒有說它會變成 null 或未分類。
- 真正的缺陷在兩處：
  - 公司目錄的載入路徑沒有排除 TDR。
  - 下游只把 null 或空字串當成 UNCLASSIFIED，所以 fallback 字串被當成真的產業。
- ISIN 路徑把 TDR 標成 unsupported。在正式執行時，上市股票不走這條路徑（html_content=None 時改走 OpenAPI），所以兩條路徑的不一致只是程式碼層面的問題。
- TDR 對大盤等權基準的影響可以忽略。5 日基準有 1077 檔可比較，其中 9 檔是 TDR；拿掉後基準從約 2.431% 變成約 2.448%，只差約 0.017 個百分點。
- 除了 finding 提到的畫面，Dashboard.tsx:338 也會顯示「35 大類股」。

**使用者情境**
- 使用者只部署台股，這 10 檔 TDR 確實在 universe 裡。
- 選股頁與儀表板的產業表都會多出一列「未知產業（91）」。
- 異常診斷在預設、不加篩選的情況下，也會出現神州-DR 的相對強弱訊號。
- 自選 9 檔都不受影響，監控規則 0 條。

**嚴重度**
- 這是明確的功能錯誤：多出一個偽產業、覆蓋率高報、用不同行業的外國公司互為同業來比較強弱。
- 影響只有 10 檔、成交值占比約 0.015%，也不會造成資料破壞，所以是 P2，不到 P1。
- 在 docs/code-review-2026-09-22.md 等 docs/*.md 裡搜尋「TDR／存託／-DR／未知產業」，沒有找到相關記錄，不是重複回報。

**驗證過程**
- 整個過程工作樹的 git status 沒有變化，data\taiwan 下也沒有新寫入的檔案。
    - 證據：
      - backend/app/taiwan/universe/industry_classification.py:23-28 docstring 說 91 是 instrument-type marker，會落入 unknown fallback；:92-93 實際回傳 f"未知產業（{value}）"（非 None）
      - backend/app/taiwan/universe/adapters.py:413-420 _official_company_directory 每一列都是 instrument_type="stock"、is_supported=True、underlying_scope="domestic"；:335 TWSE 在正式執行時（html_content=None）走這條路徑；:259-263 ISIN 路徑把 TDR 等歸為 unsupported
      - backend/app/taiwan/industry_intelligence.py:5 docstring 寫『34 canonical industries + UNCLASSIFIED tracking』；:205-213 只有 null 或空字串才變成 UNCLASSIFIED；:367-371 每個不同的字串都成為一個產業
      - backend/app/taiwan/abnormal_diagnostics.py:587-608 RELATIVE_STRENGTH_OUTLIER 以同一 industry 字串的 average_return_5d 當基準，|diff|>=10% 就發出訊號；:615-616 只對 etf 標成 not_applicable，TDR 不受保護
      - backend/app/taiwan/research_context.py:651-653 instrument_type=='stock' 且 industry 非空時，會取得『未知產業（91）』的產業脈絡
      - frontend/src/pages/TaiwanScreener.tsx:656 與 frontend/src/pages/Dashboard.tsx:338 顯示 industries.length（現為 35）；frontend 只有 api.ts:1185 的型別宣告用到 classification_coverage_pct，沒有頁面顯示這個欄位
      - docs/taiwan-market-contract-v1.md:7 『security_type v1 is stock or etf ... Warrant, bond, preferred, TDR, REIT and ETN remain deferred until both projects have verified official metadata』：把 TDR 標成 stock 違反專案契約
      - 本地 v1_master.py（讀 data/taiwan/security_master.parquet）：active 且 supported 的 stock 共 1986 檔、35 個不同產業、industry 為 null 的 0 檔；名稱含 DR 的共 10 檔，全部是 stock/domestic/未知產業（91）/TWSE_OPENAPI
      - 官方參考資料 reference\raw\twse_openapi_t187ap03_L.json：產業別 '91' 共 10 家，全部是 -DR（註冊地 BM/KY/TH/SG）；tpex_openapi_mopsfin_t187ap03_O.json 沒有 91。twse_mi_index_20260923.json 這 10 檔的成交金額加總為 128,262,626，等於 app 算出的 TDR 群組 turnover
      - 本地 v3_run.py（關閉網路、唯讀 stub、master 複本；指令：cd backend && uv run python AUDIT_SCRATCH/work/V-G6-tdr-def/v3_run.py）輸出：supported 1986、classified 1986、unclassified 0、coverage 100.0、industry_count 35、has UNCLASSIFIED False；TDR 群組 average_return_5d=0.003705、relative_strength_5d=-0.020605；DIAG 910861.TWSE 神州-DR [('RELATIVE_STRENGTH_OUTLIER','LEADER',11.76,0.37,'moderate')]
      - 獨立重算：9/23 close 4.53、9/16 close 4.04，r5=0.121287；群組 9 檔的平均 r5=0.0037046（9110 缺 9/16 基準）；diff=11.76%，與 app 相同
      - 大盤基準影響：market_equal_weight_return_5d=0.024309，comparable 1077 檔（含 9 檔 TDR）；扣除後約 0.024483，只差約 0.017 個百分點，可以忽略
      - https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&data_id=9105 → stock_name『泰金寶-DR』、industry_category『存託憑證』、type『twse』
      - https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&data_id=910861 → stock_name『神州-DR』、industry_category『存託憑證』、type『twse』
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所 ：『上市股票交易為主，亦包括指數股票型基金（ETF）、指數投資證券（ETN）、認購售權證、臺灣存託憑證等商品』，TDR 是獨立商品類型
      - https://zh.wikipedia.org/zh-tw/臺灣存託憑證 ：TDR 是『該企業已經在國外上市，另外在臺灣證券交易所申請上市』的外國企業
      - 在 docs/*.md 搜尋 TDR／存託／-DR／未知產業／（91），在 code-review-2026-09-22.md 沒有找到相關記錄，不是重複回報；驗證前後 git status 相同，data\taiwan 沒有新檔（用 find -newer 確認）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：成立，數值全部重現。官方 t187ap03_L 中產業別 91 的 10 家全是 -DR（TDR），上櫃為 0 家。OpenAPI 載入路徑（adapters.py:413-420）把它們建成 stock／domestic／supported，industry 為『未知產業（91）』。industry_intelligence 只把 null 或空字串計為 UNCLASSIFIED，因此 9/23 的快照是 coverage 100%（應為 1976/1986=99.5%）、unclassified 0（應為 10）、industry_count 35，並多出一列偽產業『未知產業（91）』：10 檔，成交金額 128,262,626 元，排第 30 名，avg_ret_5d 0.3705%，rs_5d -2.0605%，rs_20d -1.9532%。神州-DR 910861 的 5 日報酬 +12.13%，減去這個籃子的平均 +0.37% 後為 +11.76%，觸發 RELATIVE_STRENGTH_OUTLIER／LEADER／moderate。10 檔中只有這一檔觸發。需要修正的細節有三項：(a) 大盤等權 5 日基準只納入 9 檔 TDR，因為 9110 在 9/16 沒有成交；20 日基準是 10 檔。對基準的影響只有 -1.7bp 與 -1.8bp，可以忽略。(b) Dashboard.tsx:338 的『{n} 大類股』同樣顯示 35。(c) resolve_industry_name 確實照 docstring 回傳 unknown 的後備值；缺陷在於 adapter 把 TDR 標成國內股票，下游又把後備值字串當成真的產業，不是 resolve 本身違反了 docstring。
    - 理由：我用官方參考資料和本地 parquet，從頭把 2026-09-23 的產業快照重算了一遍，沒有呼叫 app 的 service 或 store，只用 polars 直接讀 security_master.parquet 和 daily/date=*/part.parquet。finding 列出的數值全部一致：
(1) 官方 t187ap03_L 共 1095 列，產業別 91 有 10 家，名稱都以 -DR 結尾；以 -DR 結尾的名稱也正好是這 10 家。上櫃 mopsfin_t187ap03_O 共 893 列，代碼 91 為 0 家。
(2) master 裡這 10 檔都是 instrument_type=stock、listing_status=active、is_supported=true、underlying_scope=domestic、industry=未知產業（91）、source=TWSE_OPENAPI，和 adapters.py:413-420 寫死的欄位一致。ISIN 路徑（adapters.py:259-263）則把非股票、非 ETF 的類別判成 unsupported，兩條路徑的處理確實不一致。
(3) 照 industry_intelligence.py:205-213 的規則重算：supported=1986、classified=1986、unclassified=0、coverage=100.0、industry_count=35。把 91 視為未分類時，classified=1976、unclassified=10、coverage=99.4965，四捨五入為 99.5，與 finding 的預期值相同。
(4) 未知產業（91）群組：supported 10、snapshot 10、traded 10、turnover 128,262,626、average_return_5d 0.003705、relative_strength_5d -0.020605，與 finding 完全相同。我另外算出 rs_20d=-0.019532；依成交金額排第 30 名（共 35 列）。
(5) 神州-DR 910861：9/16 收盤 4.04、9/23 收盤 4.53，r5=0.121287，扣掉群組平均後 diff=round(0.117582,4)=0.1176，已達 ≥0.10 的門檻，所以判成 LEADER，嚴重度 moderate（<0.15）。observed 11.76、baseline 0.37，與 finding 相同。其他 9 檔的 |diff| 都小於 0.05，只有 910861 觸發。abnormal_diagnostics.py 在 include_all=False 時保留有訊號的項目，沒有截斷，所以它會出現在預設列表。
(6) 10 檔 TDR 在 9/23 的收盤價、成交金額與成交股數，和官方 MI_INDEX 20260923 逐檔比對，差值都是 0。9/16 的 910861 收盤價不在參考資料內，Yahoo 歷史頁回 404，HiStock 抓不到表格，所以沒有外部直接佐證。不過同一個 9/16 分區中，2454 與 3034 的收盤價和官方 STOCK_DAY 完全一致；910861 的 9/16～9/23 逐日漲跌都在 10% 以內，數列本身合理。
(7) 線上來源：Yahoo 的 9105.TW 基本資料頁，產業類別顯示為「存託憑證」；Yahoo 的類股頁把「存託憑證」列為獨立類別，與 ETF、受益證券、ETN 並列。
需要修正的細節有三項：a) 大盤等權 5 日基準只納入 9 檔 TDR，因為 9110 越南控-DR 在 9/16 沒有成交、收盤為 null；20 日基準才是 10 檔。對基準的影響只有 5 日 -1.74bp、20 日 -1.82bp，可以忽略。b) 除了 TaiwanScreener.tsx:656，Dashboard.tsx:338 也會顯示「{n} 大類股」，同樣多算 1。c) resolve_industry_name 確實照 docstring 回傳了 unknown 的後備值，程式碼與 docstring 並不矛盾；真正的缺陷在於 adapter 把 TDR 標成國內股票，下游又把這個後備值字串當成真的產業。前端只有型別定義與測試引用 classification_coverage_pct，畫面不顯示，與 finding 所述相同。自選清單中，2454、3034、2379、3545 為半導體業，6196 為其他電子業，另外 4 檔是 ETF，都不受影響。整體屬於明確的功能錯誤：畫面上多出一個偽產業，還發出沒有意義的同業相對強弱訊號，但對交易口徑的數值影響極小，維持 P2 合理。
    - 證據：
      - 官方 AUDIT_SCRATCH/reference/raw/twse_openapi_t187ap03_L.json：共 1095 列，產業別=91 有 10 列（9103 美德醫療-DR、910322 康師傅-DR、9105 泰金寶-DR、910861 神州-DR、9110 越南控-DR、911608 明輝-DR、911622 泰聚亨-DR、911868 同方友友-DR、912000 晨訊科-DR、9136 巨騰-DR）；名稱以 -DR 結尾的共 10 列，全部是 91。tpex_openapi_mopsfin_t187ap03_O.json 共 893 列，代碼 91 為 0 家（輸出見 verify-tdr91\v1_out.txt）
      - data/taiwan/security_master.parquet：這 10 檔都是 instrument_type=stock、listing_status=active、is_supported=true、underlying_scope=domestic、industry=未知產業（91）、source=TWSE_OPENAPI、raw_category=股票；supported active stock 共 1986 檔（TWSE 1094、TPEX 892），industry 為 null 的 0 檔，不同的產業字串 35 個
      - backend/app/taiwan/universe/adapters.py:413-420：寫死 instrument_type="stock"、underlying_scope="domestic"、is_supported=True；adapters.py:259-263：ISIN 路徑的 else 分支為 unsupported，註解寫的是 Warrants, ETNs, TDRs
      - backend/app/taiwan/industry_intelligence.py:205-213：只有 null 或空字串才標成 UNCLASSIFIED；:367-371：每個不同的字串各成一列產業
      - 重算指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-tdr91/v2_recompute.py；輸出：total=1986 classified=1986 unclassified=0 coverage=100.0 industry_count=35；若把 91 計為未分類：classified=1976 unclassified=10 coverage=99.4965
      - 同一份輸出：d_prev=2026-09-22、d_5d=2026-09-16、d_20d=2026-08-26；[TDR group] supported=10 snapshot=10 traded=10 turnover=128262626 avg_ret_5d=0.003705 rs_5d=-0.020605 rs_20d=-0.019532 rs5_cnt=9；依成交金額排第 30 名，共 35 列
      - 同一份輸出：910861.TWSE 神州-DR close_5d=4.04、close=4.53、return_5d=0.121287、diff_5d=0.1176，觸發 rs_outlier；其他 9 檔的 |diff| 介於 0.0044 與 0.0474 之間；9110 越南控-DR 的 close_5d 為 null。對照 abnormal_diagnostics.py:587-599：observed=11.76、baseline=0.37、LEADER、moderate
      - 大盤等權基準：含 TDR 時 ew5d=0.024309（n=1077）、ew20d=0.002687（n=1086）；不含 TDR 時 ew5d=0.024483（n=1068）、ew20d=0.002868（n=1076）；差距 5 日 -1.736bp、20 日 -1.815bp。5 日只納入 9 檔 TDR，不是 10 檔
      - 官方交叉比對：twse_mi_index_20260923_quotes.csv 與本地 9/23 分區比較，10 檔 TDR 的 close、amount、volume 差值全部為 0（例如 910861 close 4.53、amount 397900、volume 88499；9105 amount 119893314）
      - v3_series.py 輸出：9/16 分區中 2454 收盤 4530、3034 收盤 548，與官方 twse_stock_day CSV 一致；910861 的 9/16→9/23 收盤依序為 4.04、4.36、4.26、4.5、4.42、4.53，逐日漲跌都在 10% 以內。9/16 的 910861 收盤價沒有外部直接佐證（Yahoo 歷史頁 404，HiStock 抓不到表格）
      - https://tw.stock.yahoo.com/quote/9105.TW/profile ：公司名稱「泰金寶-DR」，產業類別「存託憑證」
      - https://tw.stock.yahoo.com/class ：上市類股清單中有「存託憑證」一類，與 ETF、受益證券、ETN 並列
      - backend/app/taiwan/research_context.py:651-653：instrument_type=="stock" 且 industry 不為空時，就套用產業脈絡；TDR 符合這兩個條件
      - frontend/src/pages/TaiwanScreener.tsx:656 與 frontend/src/pages/Dashboard.tsx:338 都用 industries.length 顯示「大類股」數量；classification_coverage_pct 只出現在 frontend\src\lib\api.ts:1185 的型別與測試，畫面不顯示
      - 工作樹比對：稽核前後 git status 相同，只有 3 個既有的改動（daily_update.py、pnpm-lock.yaml、vite.config.d.ts），我沒有寫入 repo

### [P3 → P3] 11 檔上市 ETF 的 etf_category 與官方基金類型矛盾：主動式債券與期貨信託 ETF 被標成 foreign_equity，00716R 反向型也在其中（確認成立）

- **主張**：_official_twse_etf_directory 只在 fund_type 含『槓桿』或『反向』時分出 leveraged 或 inverse；其餘一律依『是否包含國外成分股』標成 foreign_equity 或 domestic_equity。因此官方明寫為債券 ETF、期貨信託 ETF 的產品，都被標成股票型。
- **正確的定義或數值**：依官方基金類型：債券型標 bond；期貨信託型需要獨立類別（或至少不是 equity）；00716R 標 inverse。
- **app 實際行為**：11 檔全部標成 foreign_equity，個股頁顯示『ETF · foreign_equity』，研究脈絡也把它們當成國外股票型 ETF。
- **對使用者的影響**：自選的 0050、00878、00885、00893 分類都正確，不受影響。只有查看這 11 檔 ETF 或用 AI 研究時，會看到錯誤的商品類型描述；如果之後補上 classification_source，錯誤的 category 還會直接進入稅率判斷（bond 才適用債券 ETF 稅率）。
- **修法建議**：在 _official_twse_etf_directory 依 fund_type 的關鍵字（『債券』→ bond、『期貨信託』→ 獨立的 futures 或 commodity 類別，並依 multiplier 標出 inverse）先分類，再以 scope 作為後備。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/adapters.py:441-445 （is_leveraged_type 以外只看 scope，沒有檢查 fund_type 中的『債券』『期貨』）
  - frontend/src/pages/TaiwanStockDetail.tsx:387-389 （個股頁顯示『ETF · {etf_category}』徽章）
  - backend/app/taiwan/research_context.py:706 （etf_type=inst.etf_category 進入研究脈絡；ai_research.py:247 帶進 AI 研究）
- **線上證據**：
  - https://tw.stock.yahoo.com/quote/00635U.TW/profile ：基金類型『元大標普高盛黃金ER指數股票型期貨信託基金』，追蹤『標普高盛黃金ER指數』，屬於商品期貨型，不是股票型
- **本地計算證據**：
  - a3_etf.py 交叉表（官方 fund_type 對 app 的 etf_category）：『國外成分證券主動式交易所交易基金(債券)』3 檔（00982D、00983D、00984D）→ foreign_equity；『指數股票型期貨信託基金』8 檔（00635U、00642U、00677U、00682U、00693U、00716R、00738U、00763U）→ foreign_equity。其中 00716R 的官方指數是『標普高盛布蘭特原油日報酬反向一倍ER指數』，multiplier=-1.0，category 卻不是 inverse。
  - 這 11 檔的 classification_source 都是 null。它們造成漲跌停與稅率被拒算的行為，A05 的 twse_etf_class.csv 已列出（app_refuses），本條只回報分類標籤錯誤這個新面向。
  - 明細：AUDIT_SCRATCH/work/G06-gap/etf_cmp.csv
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：_official_twse_etf_directory（adapters.py:441-445）只在 fund_type 含「槓桿」或「反向」時分出 leveraged 或 inverse，其餘一律依「是否包含國外成分股」標成 foreign_equity 或 domestic_equity。結果是官方 t187ap47_L（2026-09-22）標為「國外成分證券主動式交易所交易基金(債券)」的 3 檔（00982D、00983D、00984D），以及「指數股票型期貨信託基金」的 8 檔（00635U、00642U、00677U、00682U、00693U、00716R、00738U、00763U），在 security_master 裡都被標成 foreign_equity，classification_source 為 null。00716R 的 multiplier=-1.0，卻沒有標成 inverse。

這個標籤會以「ETF · foreign_equity」出現在個股頁，也會以 etf_type 進入研究脈絡與 AI payload。不過 AI payload 對 00716R 同時帶了 inverse=True 和 leverage_multiplier=-1.0，反向屬性沒有遺失。

原記錄沒提到的新面向：另一條進件路徑 classify_etf_provenance（adapters.py:122-124）會把同一個「主動式(債券)」基金類型判成 bond/official_metadata，把期貨信託判成 unknown，兩條路徑的分類已經不一致。

目前這 11 檔的漲跌停與稅率都被拒絕計算（fail-closed），所以錯誤只影響顯示與 AI 研究。如果之後補上 classification_source，3 檔債券 ETF 會因為 category 錯誤而得到 DOMESTIC_ETF，拿不到 BOND_ETF 稅率類別；主動式債券 ETF 在法律上是否適用債券 ETF 的證交稅停徵，本次沒有查證。自選的 0050、00878、00885、00893 分類正確，不受影響。另外，finding 引用 Yahoo 00635U 時，把「基金名稱」欄位誤寫成「基金類型」，實質結論不變。
    - 理由：我用兩種互不相依的方法重現這條 finding，兩種結果一致，也和 finding 的數字完全相同。
(1) 用 polars 讀本地 data/taiwan/security_master.parquet（271 檔上市 ETF，source 都是 TWSE_OPENAPI），和官方 t187ap47_L 參考資料（出表日期 2026-09-22，271 列，雙方代號完全對得上）交叉比對。
(2) 把 adapters._get_json 換成回傳官方原始 JSON，再用目前的程式碼重跑 _official_twse_etf_directory()，確認不是 master 過期造成的。

兩種方法都得到：官方基金類型是「國外成分證券主動式交易所交易基金(債券)」的 3 檔（00982D、00983D、00984D），以及「指數股票型期貨信託基金」的 8 檔（00635U、00642U、00677U、00682U、00693U、00716R、00738U、00763U），etf_category 全部是 foreign_equity，classification_source 全部是 null。00716R 的 leverage_multiplier 是 -1.0，category 卻不是 inverse，原因是它的 fund_type 不含「槓桿」或「反向」（adapters.py:441）。

程式碼位置已逐一核對：adapters.py:441-445 在 is_leveraged_type 以外只看 scope；TaiwanStockDetail.tsx:387-389 直接顯示 `ETF · {etf_category}`；research_context.py:706 與 ai_research.py:247 都把 etf_category 當成 etf_type 往下傳。

線上查證：Yahoo 顯示 00982D 的基金名稱是「富邦環球動態入息投資級債主動式ETF」，00635U 的基金名稱是「元大標普高盛黃金ER指數股票型期貨信託基金」、追蹤「標普高盛黃金ER指數」。兩者都證實這些產品不是股票型。

需要修正的小地方有 4 點：
(a) finding 把 Yahoo 的「基金名稱」欄位寫成「基金類型」。Yahoo 頁面抓不到基金類型欄位，不過實質結論不變。
(b) AI 研究 payload 對 00716R 也會帶 leverage_multiplier=-1.0 和 inverse=True（research_context.py:707-709、ai_research.py:248-250）。所以對 00716R 而言，「研究脈絡把它當成國外股票型」只有 etf_type 這個欄位是錯的，反向屬性並沒有遺失。
(c) finding 沒提到的新面向：另一條進件路徑 classify_etf_provenance（adapters.py:122-124）會把同一個「主動式(債券)」基金類型分成 bond／official_metadata，把期貨信託分成 unknown。也就是說，兩條進件路徑對同一份官方中繼資料已經產生分歧。
(d) 反事實驗證：把這 11 檔的 classification_source 強制設為 official_metadata 後，get_tax_class 對 3 檔債券 ETF 會回傳 DOMESTIC_ETF；再把 category 改成 bond，就會回傳 BOND_ETF（models.py:110-111）。finding 講的稅率機制因此成立。主動式債券 ETF 在法律上是否適用債券 ETF 的證交稅停徵，因為搜尋額度已經用完，我沒有上網查證。

目前這 11 檔因為 classification_source 是 null，漲跌停和稅率都會被拒絕計算（fail-closed），所以錯誤只影響顯示與 AI 研究脈絡。自選的 0050、00878 標為 domestic_equity，00885、00893 標為 foreign_equity，來源都是 official_metadata，全部正確。嚴重度維持 P3。
    - 證據：
      - 本地重現（master）：cd backend && uv run python AUDIT_SCRATCH/work/verify-g6etf/v1_crosstab.py。交叉表輸出：『國外成分證券主動式交易所交易基金(債券) | foreign_equity | null | 3』、『指數股票型期貨信託基金 | foreign_equity | null | 8』；master 中上市 ETF 共 271 列，與官方 271 列逐一對上，缺漏 0；明細存在 verify-g6etf\bond_futures_rows.csv
      - 本地重現（用目前程式碼重跑）：verify-g6etf\v2_rerun_code.py 以官方 raw\twse_openapi_t187ap47_L.json 餵給 _official_twse_etf_directory，產出 271 檔。00716R 那一列：『cat= foreign_equity | src= None | scope= foreign | mult= -1.0 | tax= REFUSED | limit= REFUSED』；其他 10 檔同樣是 foreign_equity／None
      - 數字並列：finding 寫債券 3 檔 → foreign_equity、期貨信託 8 檔 → foreign_equity、00716R 的 multiplier=-1.0、classification_source 全為 null；我算出的完全相同。G06-gap\etf_cmp.csv 對這 11 個代號也正好是 11 列
      - 官方基金類型分布（t187ap47_L，出表日期 2026-09-22）：國外成分證券指數股票型 99、國內成分證券指數股票型 66、槓桿/反向 51、國內主動(股票) 22、國外主動(股票) 8、期貨信託 8、國外成份/加掛外幣 6、境外 3、連結式 3、國外主動(債券) 3、國外平衡型 2
      - backend/app/taiwan/universe/adapters.py:441-445：`is_leveraged_type = "槓桿" in fund_type or "反向" in fund_type`，其餘情況 `category = "foreign_equity" if scope == "foreign" ...`；live 路徑在 adapters.py:335 走這個函式
      - backend/app/taiwan/universe/adapters.py:122-124：classify_etf_provenance 遇到含『債券』的 fund_type 會回傳 bond/official_metadata。實跑結果：主動(債券) → ('bond','official_metadata','foreign',1.0)；期貨信託 → ('unknown','unknown','unknown',1.0)。兩條進件路徑的分類不一致
      - frontend/src/pages/TaiwanStockDetail.tsx:387-389：`ETF · {data.identity.etf_category}`；etf_category 來自 detail_service.py:103 的 master_item.etf_category
      - backend/app/taiwan/research_context.py:706 `etf_type=inst.etf_category`；707-709 同時帶 leverage_multiplier 與 inverse。ai_research.py:247-250 會把這些欄位全部送進 AI payload，所以 00716R 的 inverse=True 仍會傳給 AI
      - backend/app/taiwan/universe/models.py:110-111：只有 cat==bond 且 multiplier==1 時才回傳 BOND_ETF。反事實實跑：強制 classification_source=official_metadata 時，00982D、00983D、00984D 得到 DOMESTIC_ETF，改成 cat=bond 後得到 BOND_ETF
      - https://tw.stock.yahoo.com/quote/00982D.TW/profile ：基金名稱『富邦優選收益傘型證券投資信託基金之富邦環球動態入息投資級債主動式ETF證券投資信託基金』（債券型）
      - https://tw.stock.yahoo.com/quote/00635U.TW/profile ：基金名稱『元大標普高盛黃金ER指數股票型期貨信託基金』，追蹤指數『標普高盛黃金ER指數』。這是名稱欄位，不是 finding 所寫的『基金類型』欄位；頁面上抓不到基金類型欄位
      - Yahoo 的 00716R 頁面回傳 HTTP 404，MoneyDJ 回『查無此ETF』，線上都沒查到。00716R 的資料以官方參考資料為準：fund_type『指數股票型期貨信託基金』，指數『標普高盛布蘭特原油日報酬反向一倍ER指數』
      - 自選 ETF（用目前程式碼重跑）：0050 與 00878 為 domestic_equity/official_metadata，00885 與 00893 為 foreign_equity/official_metadata，都正確

### [P3 → P3] Security Master 的 listing_date 混用民國 7 碼、YYYYMMDD、YYYY/MM/DD 三種格式，違反宣告的日期契約（確認成立）

- **主張**：三條載入路徑都把官方原始日期字串直接寫進 listing_date，沒有做格式轉換。模型與 API 的欄位說明卻各自宣稱是單一格式（models.py 寫 YYYY/MM/DD，detail_models.py 寫 YYYY-MM-DD）。
- **正確的定義或數值**：listing_date 統一為 API 宣告的 YYYY-MM-DD（民國年先加 1911）。
- **app 實際行為**：/detail 的 identity.listing_date 與研究脈絡的 identity.listing_date 對 0050 回傳 '0920630'、對 2454 回傳 '20010723'、對上櫃 ETF 回傳 'YYYY/MM/DD'。
- **對使用者的影響**：前端目前沒有顯示 identity.listing_date：ETF 基本資料區塊用的是 etf_data 轉換過的日期（TaiwanReferenceData.tsx:198），所以畫面上看不到錯誤。影響限於 API 使用者與日後任何依 listing_date 做計算的功能（例如新掛牌天數）；拿民國 7 碼字串直接和西元日期比較會得出錯誤結果。
- **修法建議**：在三個 adapter 寫入前統一轉成 ISO 日期（可重用 etf_data._official_date 的邏輯），並在 load_cache 時正規化既有的 parquet。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/adapters.py:415 （公司目錄『上市日期』與 DateOfListing 原樣寫入）
  - backend/app/taiwan/universe/adapters.py:448 （t187ap47_L 的『上市日期』是民國格式，原樣寫入）
  - backend/app/taiwan/universe/adapters.py:232 （ISIN 表日期 YYYY/MM/DD，原樣寫入）
  - backend/app/taiwan/universe/models.py:60 （註解寫『YYYY/MM/DD』）
  - backend/app/taiwan/detail_models.py:40 （API 說明寫『掛牌日期 YYYY-MM-DD』）
  - backend/app/taiwan/etf_data.py:191 （同一份 t187ap47_L 在 ETF 基本資料區塊會經 _official_date 轉換，可見專案已知是民國格式）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - master 格式分布：TWSE_OPENAPI 的 etf 271 列是 7 位數字，例如 00400A 為 '1150409'；stock 1,094 列與 TPEX_OPENAPI 的 stock 892 列是 8 位數字，例如 '19620209'；TPEX_ISIN 的 etf 120 列含斜線，例如 '2026/08/26'。
  - 官方原始 JSON：t187ap47_L 中 0050 的『上市日期』是 '0920630'（民國 92/06/30），refdl 轉換後的 etf_meta 為 2003-06-30；00878 是 '1090720'，即 2020-07-20。master 中 0050 與 00878 的 listing_date 分別就是 '0920630'、'1090720'；2454 是 '20010723'。
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：Security Master 的 listing_date 共有四個資料來源，經三處 adapter 程式碼原樣寫入，混用三種格式：
- TWSE 公司目錄：t187ap03_L 的「上市日期」，YYYYMMDD，1,094 列。
- TPEX 公司目錄：mopsfin_t187ap03_O 的 DateOfListing，YYYYMMDD，892 列。
- TWSE ETF 目錄：t187ap47_L 的「上市日期」，民國 7 碼，271 列。
- TPEX ISIN ETF：YYYY/MM/DD，120 列。

load_cache 讀取時也不正規化。這些字串會原封不動出現在三個地方：/detail 的 identity.listing_date、研究脈絡的 identity.listing_date，以及 to_provider_dataframe 的 list_date（service.py:283）。實例：0050 為 '0920630'，2454 為 '20010723'，上櫃 ETF 為 '2026/08/26'。這違反 detail_models.py:40 宣告的 YYYY-MM-DD，也和 models.py:60 註解寫的 YYYY/MM/DD 不一致。

目前前端與後端都沒有拿這個欄位做計算或顯示，畫面上的上市日期取自 etf_data 轉換過的日期，所以使用者看不到錯誤。風險在 API 使用者與日後的日期比較：例如 '1090720' >= '2015-01-01' 會得到 False。專案既有的 parse_taiwan_date 可以把三種格式都正確轉成 ISO。
    - 理由：我用官方參考資料和本地 parquet 各跑了一次腳本，finding 的數字全部重現，一個都沒差。
- 格式分布：master 共 2,377 列。TWSE_OPENAPI 的 etf 有 271 列，全是 7 碼數字；TWSE_OPENAPI 的 stock 1,094 列與 TPEX_OPENAPI 的 stock 892 列，全是 8 碼數字；TPEX_ISIN 的 etf 120 列，全是 YYYY/MM/DD。
- 範例值：0050 是 '0920630'、00878 是 '1090720'、2454 是 '20010723'、00400A 是 '1150409'、00411A.TPEX 是 '2026/08/26'。
- 原樣寫入：三份官方原始 JSON 的日期字串和 master 逐列比對，每一列都完全相同，沒有一列經過轉換。t187ap47_L 271 列相同、0 列不同；t187ap03_L 1,094 列相同、0 列不同；t187ap03_O 892 列相同、0 列不同。另外 t187ap03_L 與 t187ap03_O 各有 1 列不在 master 裡。原因是 master 在 9/17 產生，參考資料在 9/24 下載，這和本 finding 無關。
- API 輸出：先把 parquet 複製到我的工作目錄，用純讀取的 TaiwanSecurityMaster.load_cache 載入，再建出 TaiwanStockIdentity。結果 0050 回 '0920630'、2454 回 '20010723'、00411A.TPEX 回 '2026/08/26'，但欄位說明寫的是「掛牌日期 YYYY-MM-DD」。
- 專案其實已有轉換函式：parse_taiwan_date 能把這三種格式都正確轉成 ISO，例如 0050 轉成 2003-06-30。refdl 的 etf_meta CSV 對 0050 也是 2003-06-30、00878 是 2020-07-20，和 finding 一致。

finding 說的比較風險屬實。直接拿字串比大小會出錯：'1090720' >= '2015-01-01' 得到 False，但實際是 2020 年，應為 True；'20200720' >= '2020-07-21' 得到 True，應為 False。

補充一個原記錄沒寫到的地方：同一個原始字串也經 to_provider_dataframe 的 list_date 欄位往外流，位置是 service.py:283。

使用者影響方面，前端只在 ETF 基本資料區塊顯示 profileData.listing_date，這個值來自 etf_data 的轉換結果；identity.listing_date 沒有畫面在用。目前後端也找不到拿 listing_date 或 list_date 做計算的地方。所以現在看不到錯誤，P3 合理。

finding 有一處用語需要修正：「三條載入路徑」其實是四個資料來源（TWSE 公司目錄、TPEX 公司目錄、TWSE ETF 目錄、TPEX ISIN ETF），走三處程式碼。adapters.py:415 同時處理 TWSE 與 TPEX 兩份公司目錄。

網路核實沒做成：WebSearch 回報本 session 的搜尋額度已用完（200 次中已用 200 次）。改用已下載的官方原始 JSON 作為佐證。t187ap47_L 中 0050 的「成立日期」是 '0920625'、「上市日期」是 '0920630'，可知這是民國 7 碼格式。

工作樹檢查：派工前後 git status 相同，都只有原本就在的三個修改檔。我的產物只寫在 work\verify-listdate\ 底下。
    - 證據：
      - backend/app/taiwan/universe/adapters.py:232 listing_date = tds[2].strip() or None（TPEX ISIN ETF，原樣寫入）
      - backend/app/taiwan/universe/adapters.py:406、409、415：TWSE 讀「上市日期」、TPEX 讀 DateOfListing，都以 str(listed).strip() 原樣寫入
      - backend/app/taiwan/universe/adapters.py:448：t187ap47_L 的「上市日期」原樣寫入
      - backend/app/taiwan/universe/models.py:60 註解寫 "YYYY/MM/DD" or None；backend/app/taiwan/detail_models.py:40 description 寫「掛牌日期 YYYY-MM-DD」
      - backend/app/taiwan/detail_service.py:100 和 backend/app/taiwan/research_context.py:367 直接傳遞 listing_date，沒有轉換；backend/app/taiwan/universe/service.py:103（load_cache 不正規化）、service.py:283（provider 的 list_date 也原樣輸出）
      - backend/app/taiwan/etf_data.py:191 和 241-243：ETF profile 經 _official_date 呼叫 parse_taiwan_date 轉成 ISO；backend/app/taiwan/providers/taiwan_values.py:12-22：7 碼民國年先加 1911，並會去掉斜線
      - 本地計算指令：uv run python AUDIT_SCRATCH/work/verify-listdate/verify.py。輸出：('TPEX_ISIN','etf','YYYY/MM/DD') 120；('TPEX_OPENAPI','stock','8digit') 892；('TWSE_OPENAPI','etf','7digit') 271；('TWSE_OPENAPI','stock','8digit') 1094。和 finding 完全相同
      - 同一腳本的官方原始格式：t187ap47_L 7digit 271/271、t187ap03_L 8digit 1095/1095、t187ap03_O 8digit 893/893。與 master 逐列比對：t187ap47_L 271 列相同、0 列不同；t187ap03_L 1094 列相同、0 列不同；t187ap03_O 892 列相同、0 列不同
      - 官方原始 JSON（reference\raw\twse_openapi_t187ap47_L.json）：0050 的上市日期是 '0920630'、成立日期是 '0920625'；00878 的上市日期是 '1090720'。refdl CSV（csv\twse_t187ap47_L_etf_meta.csv）轉成 0050 的 listing_date 2003-06-30、00878 的 listing_date 2020-07-20
      - verify2.py 輸出（以 load_cache 讀 parquet 複本並建出 TaiwanStockIdentity）：0050.TWSE 為 identity.listing_date='0920630'，parse_taiwan_date 轉成 2003-06-30；2454.TWSE 為 '20010723'，轉成 2001-07-23；00411A.TPEX 為 '2026/08/26'；provider list_date 中 0050 為 '0920630'
      - 字串比較錯誤實跑：'1090720' >= '2015-01-01' 得到 False（真實日期 2020-07-20，應為 True）；'20200720' >= '2020-07-21' 得到 True（應為 False）
      - frontend/src/components/taiwan/TaiwanReferenceData.tsx:186-198：畫面上的上市日期取自 ETF profile（sections.profile），前端沒有顯示 identity.listing_date（前端全文 grep 只有這一處使用 listing_date）
      - 網路核實：WebSearch 回報本 session 額度已用完（200 次中已用 200 次），無法取得線上佐證，改以官方原始 JSON 作為依據

## 確認正確的項目

- **TAIWAN_INDUSTRY_CODE_TO_NAME 的代碼覆蓋率（industry_classification.py:36-71）**：用 polars 讀 reference 的 twse_t187ap03_L_share_capital.csv（1,095 列）與 tpex_mopsfin_t187ap03_O_share_capital.csv（893 列），統計 industry_code 分布後與對照表做集合比對。指令：cd backend && uv run python scratchpad/audit/work/G06-gap/a1_codes.py。輸出：上市使用 32 種代碼（01-06、08-12、14-18、20-31、35-38、91），上櫃使用 28 種（包括上櫃專屬的 32 文化創意業 4+22 家、33 農業科技業 4 家）。map codes not used by any official row: []；codes used but not in map: ['91']。另檢查原始 JSON：raw『產業別』與『SecuritiesIndustryCode』的值都是兩碼字串，沒有 '1' 這類缺前導零的值。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_t187ap03_L_share_capital.csv
  - 來源：AUDIT_SCRATCH/reference/csv/tpex_mopsfin_t187ap03_O_share_capital.csv
  - 來源：backend/app/taiwan/universe/industry_classification.py:36
- **34 個產業名稱，以及 2023 年新增或更名的類別（35 綠能環保、36 數位雲端、37 運動休閒、38 居家生活、16 觀光餐旅）**：(a) 與官方 MI_INDEX 2026-09-23 的價格指數表比對（本地 reference）。其中 32 個類股指數依序為水泥、食品、塑膠、紡織纖維、電機機械、電器電纜、化學、生技醫療、玻璃陶瓷、造紙、鋼鐵、橡膠、汽車、半導體、電腦及週邊設備、光電、通信網路、電子零組件、電子通路、資訊服務、其他電子、建材營造、航運、觀光餐旅、金融保險、貿易百貨、油電燃氣、綠能環保、數位雲端、運動休閒、居家生活、其他，與對照表的上市 32 類一一對應（對照表多了『工業／業』等後綴，只是命名習慣不同）。(b) 逐代碼列出官方目錄實收資本最大的 4 家成員（a4_exemplar.py），例如 24→2330 台積電、2303 聯電；31→2317 鴻海；36→8454 富邦媒、8044 網家；37→9904 寶成、9910 豐泰；38→9934 成霖、5903 全家。(c) 線上抽查 12 個代碼，全部與對照表一致：24（Yahoo：2454、3034、2379、3545 都是『半導體』）、31（Yahoo 6196『其他電子』；HiStock『上市 其他電子類股』）、32（Yahoo 3293『櫃文化創意』）、33（HiStock 1240『上櫃 農業科技業類股』）、35（HiStock 9930『上市 綠能環保』）、36（HiStock 8044『上櫃 數位雲端類類股』）、37（Yahoo 9910『運動休閒』）、38（HiStock 9934『上市 居家生活』）、16（官方指數名稱是『觀光餐旅類指數』；HiStock 2706 仍用舊名『上市 觀光類股』）、23（Yahoo 6505『油電燃氣』）、20（HiStock 9945『上市 其他類股』）、30（Yahoo 6214『資訊服務』）。Yahoo 類股總表也列出了上市的運動休閒、居家生活、數位雲端、綠能環保，以及上櫃的櫃文化創意、櫃農業科技業。附註：Yahoo 把代碼 36 的上櫃公司 8044 標成『櫃電子商務』，HiStock 標成『上櫃 數位雲端類類股』。上市的 36 官方指數名稱是『數位雲端類指數』，app 與 HiStock 以及官方代碼分組一致，判斷是 Yahoo 沿用舊標籤。另外，模組 docstring 寫『33-industry classification scheme』（industry_classification.py:21），實際是 34 個代碼，只是文件筆誤。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_mi_index_20260923_indices.csv
  - 來源：https://tw.stock.yahoo.com/class （上市類股：…運動休閒、居家生活、數位雲端、綠能環保；上櫃類股：…櫃文化創意、櫃農業科技業、櫃電子商務…）
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW/profile （產業類別：其他電子）
  - 來源：https://histock.tw/stock/6196 （上市 其他電子類股）
  - 來源：https://tw.stock.yahoo.com/quote/3293.TWO/profile （櫃文化創意）
  - 來源：https://histock.tw/stock/1240 （上櫃 農業科技業類股）
  - 來源：https://histock.tw/stock/9930 （上市 綠能環保）
  - 來源：https://histock.tw/stock/8044 （上櫃 數位雲端類類股）
  - 來源：https://tw.stock.yahoo.com/quote/8044.TWO/profile （櫃電子商務）
  - 來源：https://tw.stock.yahoo.com/quote/9910.TW/profile （運動休閒）
  - 來源：https://histock.tw/stock/9934 （上市 居家生活）
  - 來源：https://histock.tw/stock/2706 （上市 觀光類股）
  - 來源：https://tw.stock.yahoo.com/quote/6505.TW/profile （油電燃氣）
  - 來源：https://histock.tw/stock/9945 （上市 其他類股）
  - 來源：https://tw.stock.yahoo.com/quote/6214.TW/profile （資訊服務）
- **Security Master（data/taiwan/security_master.parquet，updated_at 2026-09-17）的股票產業欄位與官方目錄一致**：a2_master.py 以 code+exchange 為鍵，用 full join 比對 master 的 stock 列（上市 1,094、上櫃 892）與官方目錄。兩邊都有的 1,986 檔中，resolve_industry_name(master) 與 resolve_industry_name(官方代碼) 不符的有 0 筆。只在官方目錄出現的 2 筆是 7812 稜研科技*-創與 7856 漢測，都在 2026-09-22 掛牌，屬於已知的 F9，不重複回報。
  - 來源：data/taiwan/security_master.parquet
  - 來源：AUDIT_SCRATCH/work/G06-gap/a2_master.py
- **自選 5 檔股票的 industry（2454、3034、2379、3545、6196）**：三方比對。master 的值：2454、3034、2379、3545 是半導體業，6196 是其他電子業。官方原始 JSON 的 t187ap03_L『產業別』：2379、2454、3034、3545 是 '24'，6196 是 '31'。線上來源：Yahoo 奇摩的產業類別 2454、3034、2379、3545 是『半導體』，6196 是『其他電子』；HiStock 6196 是『上市 其他電子類股』。Goodinfo 回應 HTTP 403，抓不到。
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW/profile （半導體）
  - 來源：https://tw.stock.yahoo.com/quote/3034.TW/profile （半導體）
  - 來源：https://tw.stock.yahoo.com/quote/2379.TW/profile （半導體）
  - 來源：https://tw.stock.yahoo.com/quote/3545.TW/profile （半導體）
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW/profile （其他電子）
  - 來源：https://histock.tw/stock/6196
  - 來源：AUDIT_SCRATCH/reference/raw/twse_openapi_t187ap03_L.json
- **resolve_industry_name 的邊界處理（industry_classification.py:74-94），以及 load_cache 讀取時的正規化（service.py:110）**：讀程式碼：None 或空白回傳 None；已知代碼回傳名稱；未知的純數字回傳『未知產業（x）』；其他字串原樣回傳（idempotent）。執行 uv run python -m pytest tests/test_taiwan_industry_classification.py -q -p no:cacheprovider，結果 10 passed。master 目前存的已經是可讀名稱，原始值中沒有數字代碼殘留（只剩 91 的 fallback 字串）。
  - 來源：backend/app/taiwan/universe/industry_classification.py:85
  - 來源：backend/app/taiwan/universe/service.py:110
  - 來源：backend/tests/test_taiwan_industry_classification.py
- **上市 ETF 的 instrument_type 與 underlying_scope 對照官方 t187ap47_L 的 includes_foreign_components**：a3_etf.py 以 code 做 full join，master 的 TWSE etf 271 列對 twse_t187ap47_L_etf_meta.csv 271 列，兩邊都沒有多出的列。instrument_type 全部是 etf。raw_category 與 fund_type 不符 0 筆。scope 不符 0 筆：是對 foreign、否對 domestic（adapters.py:431）。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_t187ap47_L_etf_meta.csv
  - 來源：backend/app/taiwan/universe/adapters.py:431
  - 來源：AUDIT_SCRATCH/work/G06-gap/etf_cmp.csv
- **leverage_multiplier（adapters.py:84-94）**：逐檔比對官方 fund_type『槓桿/反向指數股票型基金』51 檔的 index_name：凡是『正向兩倍、2倍槓桿、兩倍槓桿』的都是 2.0（例如 00631L 臺灣50指數、00663L 發行量加權日報酬正向兩倍）；凡是『反向一倍、反向』的都是 -1.0（例如 00632R、00664R）。51/51 一致。唯一不在槓桿類、代碼卻以 R 結尾的 00716R，官方指數是『標普高盛布蘭特原油日報酬反向一倍ER指數』，app 給 -1.0，也正確。上櫃 ETF 沒有任何 multiplier≠1 的列，也沒有名稱含正或反的列。
  - 來源：AUDIT_SCRATCH/work/G06-gap/etf_cmp.csv
  - 來源：backend/app/taiwan/universe/adapters.py:90
- **自選 4 檔 ETF 的分類（0050、00878、00885、00893）**：master 的值：0050、00878 是 domestic_equity／official_metadata／domestic／1.0；00885、00893 是 foreign_equity／official_metadata／foreign／1.0。官方 fund_type：0050、00878 是『國內成分證券指數股票型基金』、是否含國外成分股為『否』；00885、00893 是『國外成分證券指數股票型基金』、『是』。四檔全部一致。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_t187ap47_L_etf_meta.csv
  - 來源：data/taiwan/security_master.parquet
- **選股的產業篩選與產業情報名稱一致**：讀程式碼：選股的 industry 欄取自 Security Master（screener.py:287），篩選條件是等值比對（screener.py:472-473）。前端的產業按鈕文字直接取自產業情報的 industries[].industry（TaiwanScreener.tsx:700-711），兩邊出自同一欄位，不會因為名稱不一致而篩空。
  - 來源：backend/app/taiwan/screener.py:472
  - 來源：frontend/src/pages/TaiwanScreener.tsx:705

## 無法驗證的項目

- **官方代碼表中目前沒有公司使用的代碼（例如 19 綜合、34 電子商務、80 管理股票）是否存在，以及正式名稱**：依規定不能連 twse.com.tw、tpex.org.tw 與 mops；WebSearch 顯示本 session 的搜尋額度已用完（200/200），沒有執行。這些代碼沒有出現在 9/22、9/23 的官方目錄中，所以目前沒有影響。之後如果出現，會被 resolve_industry_name 顯示成『未知產業（xx）』，並與 91 一樣被算進已分類（見 finding 1）。
- **對照表名稱與 ISIN 表（isin.twse.com.tw strMode=2/4）『產業別』欄的逐字一致性，以及 industry_classification.py:32-35 宣稱的『零衝突』驗證**：isin.twse.com.tw 屬於 twse.com.tw，依規定禁止連線，reference 也沒有下載 ISIN 表。本次只驗證了語意與代碼分組（見 verified_ok），『化學工業』與『化學類』、『生技醫療業』與『生技醫療類』這類字尾差異無法和 ISIN 原文逐字核對。
- **櫃買中心對代碼 36 的正式中文名稱（數位雲端或電子商務）**：第三方說法不一致：Yahoo 把 8044 標成『櫃電子商務』，HiStock 標成『上櫃 數位雲端類類股』，而官方櫃買網站不能連。證交所端 36 的官方指數名稱是『數位雲端類指數』（本地 MI_INDEX），app 的名稱與它一致。
- **上櫃 ETF（120 檔，來源 TPEX_ISIN）的 etf_category、scope 與 multiplier**：reference 只有上市 ETF 的 t187ap47_L，沒有上櫃 ETF 的官方基本資料；CEOGEU 誤判已由 A05 記錄，依指示不重複。
- **Goodinfo 的產業別三方比對**：https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID=8044 回應 HTTP 403 Forbidden，抓不到內容；改用 Yahoo 奇摩與 HiStock。
