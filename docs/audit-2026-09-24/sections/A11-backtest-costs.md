# A11-backtest-costs｜回測、交易成本與交易規則

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 8 | 3 | 0 | 0 | 0 | 15 | 7 |

## 稽核者摘要

結論：這個部署下，回測撮合的日期與價格對齊正確，統計公式也和獨立重算一致；但成本、漲跌停、除權息、交易單位四項都沿用 A 股規則，或與台股規則不符。重點如下：(1) 所有回測 API 的預設成本都是 A 股口徑：佣金 0.02% 雙邊、證交稅 0；挖掘 API 的 stamp_tax 0.05% 是 A 股 2023 年起的印花稅率。TaiwanMarketProfile 從未接進正式呼叫鏈。用官方 8～9 月日 K 實跑 6 檔自選股，每筆報酬比台股牌告成本（0.1425%×2＋0.3%／ETF 0.1%）高估 0.24～0.45 個百分點。(2) 回測的漲跌停鎖死判定用 A 股「四捨五入到 0.01」計算漲跌停價，不是台股「依升降單位，漲停往下取、跌停往上取」。對照證交所 TWT49U 官方漲跌停價：一般股 781 筆中只有 52 筆完全相符（台股 PriceLimitModel 相符 758 筆）。9/23 以漲跌停價收盤的 16 檔只偵測到 2 檔，一字跌停的 6225 仍可賣出。(3) 台股 enriched 價格是未還原價（close＝raw_close＝官方收盤），回測也不計股利。2379 除息期間的交易被算成 −1.26%，含息實際是 +2.24%；00878、2379 各有一次 3% 停損是被除息缺口誤觸。(4) minute_fill 的參考線 MA5 含成交當日收盤，是未來函數。(5) 正式撮合寫死每單位 100 股（A 股一手），台股整股 1000 股、零股 1 股都沒照規則。其餘為 P3：ETF 稅率、年化用 252 日、步進優化的窗口以自然日計、基準指數寫死上證等。可及性：前端沒有回測頁面，挖掘排程也關閉（preferences 無此鍵，預設 False），data/backtest_results 是空的。所以使用者目前只會透過直接呼叫 HTTP API 碰到這些問題。稽核全程唯讀，repo 與 data 沒有任何寫入（git status 與開始時相同）。網路請求共約 19 次，沒有連證交所或櫃買中心。

## 問題清單

### [P1 → P1] 回測與挖掘的預設交易成本是 A 股口徑，台股成本模型沒有接上（確認成立）

- **主張**：策略、因子、優化、步進回測的預設是 fees_pct=0.0002（雙邊佣金）、stamp_tax 未設（=0）、滑價 5bps；挖掘的預設是 commission 0.0002＋stamp_tax 0.0005（A 股 2023-08-28 起的印花稅率）。MatcherConfig 從未傳入 market_profile，所以台股的 0.1425% 手續費與賣出 0.3%／0.1% 證交稅都不會生效。
- **正確的定義或數值**：台股部署下，預設成本應是台股口徑：買賣各收手續費（費率可設定，牌告 0.1425%，可設折扣與每筆最低費用），賣出另收證交稅（一般股 0.3%、ETF 0.1%、債券 ETF 在期限內 0%）；或像 contract v1 那樣要求使用者明確設定佣金，而不是靜默套用 A 股數值。
- **app 實際行為**：預設只扣 0.02%×2 的佣金，不扣證交稅；挖掘改扣 A 股 0.05% 印花稅。TaiwanMarketProfile 只有 legacy 的 simulate() 與測試會用到。
- **對使用者的影響**：任何台股回測、因子分層、參數優化或策略挖掘的報酬、勝率、Sharpe 都被系統性高估：每筆來回約 0.45 個百分點（一般股），高週轉策略會被誤判為可獲利。目前前端沒有回測或挖掘頁面（frontend/src/lib/navigation.ts:25-31），挖掘排程預設關閉、使用者的 preferences 也沒有開啟，data/backtest_results 是空的，所以只有直接呼叫 API 時才會受影響。
- **修法建議**：由 asset market 決定成本：台股路徑預設建立 TaiwanMarketProfile（或要求 API 明確給佣金與折扣），賣出稅率依證券類別分流；mining 與 factor 也改用同一套成本模型，並補上台股成本的回歸測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:56-59 fees_pct 0.0002、slippage_bps 5.0
  - backend/app/backtest/engine.py:95-101 未設 stamp_tax_pct 時 stamp=0.0
  - backend/app/api/backtest.py:87,144,194,334,471,782,1005 fees_pct 預設 0.0002
  - backend/app/api/mining.py:59-61 commission 0.0002、stamp_tax 0.0005
  - backend/app/backtest/strategy.py:1199-1220 建 MatcherConfig 時沒有 market_profile；grep 'market_profile' 只命中 engine.py
  - backend/app/backtest/factor.py:1058-1066 因子回測來回成本＝2×0.0002＋0＋2×滑價
  - docs/taiwan-market-contract-v1.md:25-26 明訂證交稅為賣方 0.3%／ETF 0.1%，且佣金是券商設定、沒有法定預設
- **線上證據**：
  - https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2條：「公司發行之股票及表明股票權利之證書或憑證徵千分之三。二、公司債及其他經政府核准之有價證券徵千分之一。」
  - https://quantpass.org/stock-commission/ 「股票交易金額 x 0.1425%」買進和賣出都需支付；「最低收費為20元新台幣」；賣出「股票賣出金額 x 0.3%」、「ETF則為 0.1%」
  - https://www.kgi.com.tw/zh-tw/support-index/trading-regulations（搜尋結果摘要）：「證券經紀商收取證券交易手續費得按客戶成交金額自行訂定費率標準，另得訂定折讓及每筆委託最低費用」
  - http://finance.people.com.cn/n1/2023/0828/c1004-40065300.html、http://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230827_5725662.shtml（搜尋結果摘要）：A 股證券交易印花稅自 2023-08-28 起減半，由 0.1% 降為 0.05%，向賣方單邊徵收（＝mining 預設的 0.0005）
- **本地計算證據**：
  - 指令：uv run python scratchpad\audit\work\A11\verify_engine.py（輸出 verify_engine.out Part 5）
  - 用官方日 K 在 8/06 開盤買、8/24 開盤賣，股數與 app 相同，每筆報酬（app 預設 vs 台股牌告）：0050 1.2141% vs 0.9664%；00878 −2.0696% vs −2.3093%；2454 −6.9596% vs −7.3735%；3034 −3.2434% vs −3.6738%；3545 0.6590% vs 0.2113%；6196 −2.0335% vs −2.4692%。app 高估 0.24～0.45 個百分點
  - 來回成本率：app 預設 0.1400%（其中滑價 0.1000%，不含滑價只有 0.0400%）；台股牌告一般股 0.5850%、ETF 0.3850%；六折以下的 28% 折扣為 0.3798%；挖掘預設 0.0900%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：主張成立。以下所有回測路徑的預設成本都是 A 股口徑：策略、優化、步進回測、因子、舊的 /api/backtest/run，以及挖掘。
1. 策略、優化、步進回測、因子與舊的 /api/backtest/run：只扣雙邊 0.02% 佣金，外加雙邊各 5bps 滑價，完全不扣證交稅。
2. 挖掘：另外改扣 0.05% 賣方印花稅，這正是 A 股 2023-08-28 起的稅率。
3. 正式程式碼從不傳入 market_profile，所以 TaiwanMarketProfile 只有測試會用到。依法規，台股應是買賣各收手續費（牌告 0.1425%，券商可自訂與折讓，常見每筆最低 20 元），賣出另收證交稅：一般股 0.3%、ETF 0.1%、債券 ETF 到 2026-12-31 為 0%、當沖 0.15%。
4. 不含滑價比較，app 每筆來回少扣：一般股 0.445 個百分點、ETF 0.245 個百分點。
5. 使用者情境：前端沒有回測頁，挖掘排程也是關閉的，目前只有直接呼叫 API 才會觸發，屬於潛伏缺陷。
6. 補充新面向：即使接上 TaiwanMarketProfile，MatcherConfig.sell_cost_pct 仍然一律套用 0.3%，不分 ETF，也不處理最低手續費；矩陣撮合也以 A 股的 100 股為單位下單。修正時必須依證券類別決定稅率，並一併改用台股的交易單位（1000 股或零股 1 股）。
    - 理由：我照現況重讀了 finding 引用的程式碼，也自己跑了成本計算，主張成立。

一、程式碼層面
1. MatcherConfig 的預設是 fees_pct=0.0002、stamp_tax_pct=None、slippage_bps=5.0。沒有 market_profile 時，賣出稅率取 0.0（engine.py:56-59、95-101）。
2. engine.py:75 的註解寫明「未设置时回退 A 股默认规则」；services/backtest.py:85 標注「万二佣金」。由此可確認這組預設本來就是 A 股口徑。
3. 全 app 只有一處建立 MatcherConfig（strategy.py:1199-1220），沒有傳入 market_profile。TaiwanMarketProfile 在 app 內只出現在 market_rules.py 的定義與 taiwan/__init__.py 的匯出，其餘都在 tests。
4. finding 說「只有 legacy simulate() 會用到」，這句要修正：legacy 路徑確實會讀 config.market_profile（engine.py:2536、2662），但正式程式碼從來沒有傳這個欄位進去。
5. 挖掘的預設是 commission 0.0002＋stamp_tax 0.0005，有三處都這樣設：api/mining.py:59-60、mining_runtime.py:895-899、mining_candidates.py:421-422。因子回測的單次成本是 2×佣金＋印花稅＋2×滑價（factor.py:1059-1067）。

二、正確規則（我另找的權威來源）
1. 全國法規資料庫的《證券交易稅條例》（pcode G0340078，修正日 114-01-02）：股票千分之三；公司債及其他經政府核准的有價證券千分之一；當沖千分之一點五到 2027-12-31；債券 ETF 免稅到 2026-12-31（民國 115-12-31）。這個來源跟 finding 用的 law-out.mof.gov.tw 不同。
2. 凱基證券牌告頁，我直接抓取成功：手續費是「成交價金X股數X0.001425」，買賣雙邊都收，每筆最低 20 元，賣出另收 0.3%。
3. 中文維基「印花税」：2008-09-19 起改為「由卖方按1‰的税率缴纳」，「2023年8月28日起，证券交易印花税实施减半征收」，也就是 0.05%。這證明挖掘預設的 0.0005 正是 A 股現行印花稅。
4. 專案自己的 docs/taiwan-market-contract-v1.md:25-26 也規定賣方證交稅一般股 0.3%、ETF 0.1%，且佣金屬於券商設定。

三、本地計算（使用 app 本身的類別）
1. app 預設來回成本 0.0014，不含滑價只有 0.0004。
2. 挖掘預設來回成本 0.0019，不含滑價 0.0009。
3. 掛上 TaiwanMarketProfile 後是 0.00685，不含滑價 0.00585。
4. 台股牌告一般股 0.00585、ETF 0.00385，二八折時一般股 0.003798。
5. 不含滑價比較，app 每筆來回少扣：一般股 0.445 個百分點、ETF 0.245 個百分點。這與 finding 的「0.24～0.45」一致。

四、嚴重度
CONTRIBUTING 第 11 節把「錯誤金融口徑」列為 P1，這個缺陷正是這一類，所以維持 P1。

五、使用者情境下會不會發生
目前是潛伏狀態，finding 自己也如實揭露了：
1. 前端已移除 /backtest 頁。frontend/src/lib/navigation.ts:25-31 的註解說明 route 與元件都已刪除；對回測、因子、挖掘 API 的呼叫只出現在 api.ts 與 miningTask.ts，沒有頁面使用。
2. preferences.json 沒有任何 mining_ 開頭的鍵，因此預設是關閉的（preferences.py:539）。
3. data/backtest_results 是空的。
所以只有直接呼叫 API 時才會觸發。

六、原記錄沒提到的新面向（在這裡補充）
1. 即使照 fix 建議預設掛上 TaiwanMarketProfile，MatcherConfig.sell_cost_pct 仍然一律取 tax.ordinary_stock_rate，也就是 0.3%（engine.py:97-99），完全不看 ETF 的 0.1%。而且它是純百分比，不會套用每筆最低手續費。使用者自選清單中的 0050、00878、00885、00893 會被多扣稅。所以修正必須依證券類別決定稅率，不能只把 profile 接上。
2. 正式使用的矩陣撮合以 100 股為單位下單（engine.py:2108）；independent 路徑固定 100 股（engine.py:1027-1028）。這是 A 股一手的大小。台股是一張 1000 股或零股 1 股，維基「臺灣證券交易所」頁寫的是「一般股票以1,000股為一交易單位」。只有 legacy 路徑會讀 profile 的 lot_size。
3. 舊的 /api/backtest/run 走 vectorbt，fees 是 0.0002、沒有證交稅（services/backtest.py:85-86、266-267），同樣是 A 股口徑。
    - 證據：
      - backend/app/backtest/engine.py:56-59：fees_pct=0.0002、commission_pct=None、stamp_tax_pct=None、slippage_bps=5.0。
      - backend/app/backtest/engine.py:75-76：註解寫「未设置时回退 A 股默认规则」，且 market_profile 預設 None。
      - backend/app/backtest/engine.py:95-101：有 profile 時一律取 tax.ordinary_stock_rate（不分 ETF）；沒有 profile 時印花稅為 0.0。
      - backend/app/services/backtest.py:85-86：fees_pct 0.0002 標注「万二佣金」；266-267 以 vectorbt fees=0.0002 計算，沒有證交稅。
      - backend/app/backtest/strategy.py:1199-1220：這是全 app 唯一建立 MatcherConfig 的地方（grep 'MatcherConfig(' 只命中這裡），沒有傳 market_profile。
      - grep 'TaiwanMarketProfile' backend\app 只命中 market_rules.py:10,356 與 taiwan\__init__.py:47,72；其餘都在 tests\test_taiwan_backtest.py 與 test_taiwan_market_rules.py。
      - backend/app/api/backtest.py:87,144,194,334,471,782,1005：預設都是 fees_pct: float = 0.0002；commission_pct 與 stamp_tax_pct 預設 None。
      - backend/app/api/mining.py:59-60：commission_pct Field(0.0002)、stamp_tax_pct Field(0.0005)。mining_runtime.py:895-899 與 mining_candidates.py:421-422 用的是同一組預設。
      - backend/app/backtest/factor.py:1059-1067：return 2.0 * commission + stamp_tax + 2.0 * slippage。
      - backend/app/backtest/engine.py:2108：shares = np.floor(allocation / (entry_price * (1 + buy_cost_pct)) / 100) * 100，是 A 股 100 股一手（新面向）。
      - docs/taiwan-market-contract-v1.md:25-26：證交稅是賣方負擔，一般股 0.3%、ETF 0.1%；佣金屬於券商設定。
      - 指令：uv run python scratchpad\audit\work\V-cost-def\check_cost.py。輸出：default roundtrip 0.0014（ex-slip 0.0004）；mining roundtrip 0.0019（ex-slip 0.0009）；tw profile roundtrip 0.00685（ex-slip 0.00585）；台股牌告 stock 0.00585、etf 0.00385、stock@28% 0.003798；FactorConfig round trip 0.0014；StrategyBacktestRequest 預設 fees_pct 0.0002、stamp_tax_pct None。
      - https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340078（證券交易稅條例，修正日 114-01-02）：第2條「公司發行之股票…徵千分之三」「公司債…徵千分之一」；第2-1條債券 ETF 免稅「至一百十五年十二月三十一日止」；第2-2條當沖「千分之一點五」到 2027-12-31。
      - https://www.kgi.com.tw/zh-tw/support-index/trading-regulations（直接抓取成功，頁面為靜態內容）：手續費「成交價金X股數X0.001425」，買賣雙邊都收，每筆最低 NT$20，賣出另收 0.3%。
      - https://zh.wikipedia.org/wiki/印花税：「2008年9月19日，证券交易印花税由双边征收改为单边征收，由卖方按1‰的税率缴纳」；「2023年8月28日起，证券交易印花税实施减半征收」，減半後即 0.05%，與挖掘預設 0.0005 相同。
      - https://zh.wikipedia.org/wiki/臺灣證券交易所：「一般股票以1,000股為一交易單位，未滿1,000股可透過零股交易」。
      - 使用者情境：frontend\src\lib\navigation.ts:25-31 的註解說明 /backtest 的 route 與元件已刪除；對回測、因子、挖掘 API 的呼叫只出現在 api.ts 與 miningTask.ts。data\user_data\preferences.json 裡沒有 mining_ 開頭的鍵，preferences.py:539 預設為 False。data\backtest_results 是空目錄。
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：核心說法成立。策略、因子、優化、步進回測的預設是雙邊佣金 0.02%、不扣證交稅、雙邊滑價各 5bps，來回合計 0.14%。挖掘改用佣金 0.02%＋A 股印花稅 0.05%＋滑價，來回合計 0.19%，不含滑價是 0.09%。正式程式碼從未建立 TaiwanMarketProfile，也從未把它傳給 MatcherConfig，所以台股的手續費與證交稅都沒有生效。

我用官方日 K 在 8/06 開盤買、8/24 開盤賣，重現了 finding 的每筆報酬，誤差在 0.0001 個百分點以內。但 finding 的「高估 0.24～0.45 個百分點」是拿含滑價的 app 值和不含滑價的台股牌告相比。如果兩邊用同樣的 5bps 滑價，每筆報酬被高估的幅度是一般股 0.51～0.55 個百分點、ETF 0.34～0.35 個百分點（來回成本 0.685%／0.485% 對 0.14%）。

另外有一個 finding 沒提到的問題：engine.py:97-99 的 market_profile 分支對所有標的都扣 0.3% 證交稅，也忽略最低手續費。所以修法如果只是「預設建立 TaiwanMarketProfile」，ETF 每筆會被多扣約 0.2 個百分點，還必須依證券類別分流稅率。

目前前端沒有任何頁面呼叫這些 API，挖掘排程也是關閉的，所以只有直接呼叫 API 時才會受影響。
    - 理由：這條 finding 成立，所有數值都重現了。我用本機腳本直接 import 程式裡的 dataclass，並用 AST 解析 API 的預設值（沒有 import API 模組），確認成本的預設值和 finding 說的一樣：
- MatcherConfig()、StrategyBacktestConfig、FactorConfig 都是 fees_pct=0.0002、stamp_tax_pct=None、slippage_bps=5.0、market_profile=None。
- 來回成本 0.14%，其中滑價 0.10%。
- MiningStartRequest 的預設是 0.0002／0.0005／5.0。
- 整個 app 裡沒有任何地方建立 TaiwanMarketProfile；StrategyBacktestConfig 本身也沒有 market_profile 欄位。

我用官方 TWSE 日 K 參考資料，在 8/06 開盤買、8/24 開盤賣，並與本機 kline_daily_enriched 的開盤價比對，9 檔差值全部是 0。報酬率照 engine.py:1027-1030 的公式算。finding 列的 6 檔，app 預設值完全一致；台股牌告值有 4 檔差 0.0001 個百分點，屬四捨五入誤差。差距範圍 0.24～0.45 個百分點也重現了。

補充三點，都不會推翻結論：
(1) finding 用的比較基準不一致。app 預設含雙邊各 5bps 滑價，「台股牌告」卻不含滑價。如果兩邊用同樣的滑價（也就是 engine 接上 profile 後的算法），每筆報酬的高估是一般股 0.51～0.55 個百分點、ETF 0.34～0.35 個百分點。所以 finding 其實低估了差距。「挖掘預設 0.09%」同樣是不含滑價的數字，含滑價是 0.19%。
(2) 新面向：engine.py:97-99 在有 market_profile 時一律用 ordinary_stock_rate 0.3%，沒有 ETF 分支，也忽略最低手續費。所以修法如果只是「預設建立 TaiwanMarketProfile」，ETF 會被多扣 0.2 個百分點。例如 0050 在 profile 路徑算出 0.6630%，正確值是 0.8653%（含滑價）。
(3) 使用者影響跟 finding 說的一樣：前端 api.ts 雖然有 strategyBacktestRun 和 mining 的包裝函式，但沒有任何元件呼叫；mining_schedule_enabled 預設 False，preferences.json 裡也沒有這個鍵；data/backtest_results 是空的。

嚴重度依 CONTRIBUTING.md:295 的「錯誤金融口徑」維持 P1。線上來源方面：證交稅條例第 2 條（稅率千分之三／千分之一、向出賣人課徵）和上交所 2023-08-28 印花稅減半公告，我都自己抓到原文。WebSearch 在本 session 已經用完額度，所以 A 股減半後 0.05% 這個數值是依「原本 0.1% 減半」推算，沒有另外找到原文。確認完後我比對了工作樹快照，沒有任何改動。
    - 證據：
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-twcost/verify_costs.py（輸出存在同目錄 verify_costs.out，exit=0）
      - 預設值：MatcherConfig() 的 buy_cost_pct=0.000700、sell_cost_pct=0.000700、來回 0.001400；StrategyBacktestConfig 預設 fees_pct=0.0002、stamp_tax_pct=None、slippage_bps=5.0，而且 hasattr(market_profile)=False；FactorBacktestService._round_trip_cost(預設)=0.001400，套 mining 預設則是 0.001900
      - API 預設值（AST 解析）：backend/app/api/backtest.py 的第 87、194、334、471、782、1005 行都是 fees_pct 0.0002。第 144 行（FactorBacktestRequest）同樣是 0.0002，但這個值是讀原始碼確認的，腳本沒有抓到。backend/app/api/mining.py:59-61 是 commission 0.0002、stamp_tax 0.0005、slippage 5.0
      - backend/app/backtest/strategy.py:1199-1220 建立 MatcherConfig 時沒有傳 market_profile；grep 'TaiwanMarketProfile(' 在 app\ 下只命中類別定義 app\taiwan\market_rules.py:356，其他實例化都在 tests\test_taiwan_backtest.py 和 tests\test_taiwan_market_rules.py
      - 每筆報酬並列（我算出的 app 預設／台股牌告不含滑價，對照 finding 的數字）：0050 1.2141／0.9663 vs 1.2141／0.9664；00878 −2.0696／−2.3094 vs −2.0696／−2.3093；2454 −6.9596／−7.3735 vs −6.9596／−7.3735；3034 −3.2434／−3.6739 vs −3.2434／−3.6738；3545 0.6590／0.2112 vs 0.6590／0.2113；6196 −2.0335／−2.4693 vs −2.0335／−2.4692。差值在 0.0001 以內，是四捨五入造成的
      - 差距（不含滑價的基準）：一般股 0.4139～0.4478、ETF 0.2387～0.2479 個百分點，與 finding 說的 0.24～0.45 相符。兩邊都含 5bps 滑價時，一般股 0.5066～0.5481、ETF 0.3359～0.3488 個百分點
      - 來回成本率：app 預設 0.1400%（不含滑價 0.0400%）；牌告一般股 0.5850%（含滑價 0.6850%）；ETF 0.3850%（0.4850%）；28% 折扣一般股 0.3798%；挖掘預設不含滑價 0.0900%、含滑價 0.1900%
      - 價格資料交叉核對：官方 csv\twse_stock_day_202608_202609.csv 的 08-06／08-24 開盤價，和本機 data\kline_daily_enriched\date=2026-08-06、date=2026-08-24 的 part.parquet 比對，9 檔 18 筆的差值全部是 0.0
      - 新面向：backend/app/backtest/engine.py:97-99 的 profile 分支固定用 tax.ordinary_stock_rate（0.3%），沒有 ETF 分支，也沒有用 minimum_commission。實測 MatcherConfig(market_profile=TaiwanMarketProfile()) 的 sell=0.004925，0050 報酬是 0.6630%，正確的 ETF 口徑是 0.8653%
      - 只在 simulate_portfolio_legacy 裡讀取 market_profile 的地方：engine.py:2536（can_same_day_exit）、engine.py:2662-2663（lot_size）。這兩處都在 engine.py:2200 開始的 simulate_portfolio_legacy 裡
      - 線上來源 https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2條：「證券交易稅向出賣有價證券人按每次交易成交價格依左列稅率課徵之：一、公司發行之股票及表明股票權利之證書或憑證徵千分之三。二、公司債及其他經政府核准之有價證券徵千分之一。」
      - 線上來源 http://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230827_5725662.shtml：「自2023年8月28日起，证券交易印花税实施减半征收。」頁面上沒有寫具體稅率，0.05% 是依原本 0.1% 減半推算的
      - 線上來源 https://quantpass.org/stock-commission/：買進手續費「股票交易金額 x 0.1425%」、最低「20 元新台幣」；賣出「股票賣出金額 x 0.3%」、ETF「0.1％」
      - 影響範圍：frontend\src 裡沒有任何元件呼叫 api.strategyBacktestRun 或 miningTask（grep 沒有命中）；backend\app\services\preferences.py:539 的 mining_schedule_enabled 預設 False，data\user_data\preferences.json 沒有 mining 相關的鍵；data\backtest_results 是空目錄
      - 唯讀確認：worktree-snapshot.ps1 前後比對顯示 'no change since baseline'，exit=0

### [P1 → P1] 回測的漲跌停鎖死判定用 A 股「0.01 四捨五入」算價，漏判大多數台股漲跌停（確認成立）

- **主張**：正式回測（parquet 矩陣路徑）以 numpy_limit_price 重新計算漲跌停價，做法是把基準價乘 1.1／0.9 後四捨五入到 0.01。台股的規則是依升降單位取整：漲停往下取、跌停往上取。算出的漲停價常高於官方、跌停價常低於官方，於是「收在漲跌停」判不成鎖死，一字漲停可以買進、一字跌停可以賣出。
- **正確的定義或數值**：台股標的的鎖死判定應使用台股升降單位取整（market_rules.PriceLimitModel／MarketProfileBridge），或直接採用 enriched 已正確計算的 signal_limit_up／signal_limit_down。
- **app 實際行為**：沿用 A 股 0.01 四捨五入。9/23 漏判 14/16，包含一字跌停的 6225。
- **對使用者的影響**：台股回測會在實際買不到的一字漲停日買進、在實際賣不掉的一字跌停日賣出。這會高估追價與突破類策略的績效，低估停損失效的風險。目前只有直接呼叫 API 才會觸及（沒有前端頁面）。
- **修法建議**：_limit_lock_matrices 對 .TWSE／.TPEX 改用 TickSizeModel.round_price_limit_boundary（依 ETF／股票分類），或讀 enriched 的 signal_limit_up／down。補一個用 TWT49U 官方漲跌停價做為 fixture 的測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/price_limits.py:242-259 numpy_limit_price：cents=floor(prev×100+0.5)，結果＝(cents×110+50)//100/100
  - backend/app/backtest/matrix.py:2224-2238 up_locked = raw ≥ up_price−0.005；down_locked = raw ≤ down_price+0.005
  - backend/app/backtest/matrix.py:1014-1023 parquet 路徑的 limit lock 一律由 _limit_lock_matrices 重算，不讀 enriched 的 signal_limit_up／down（matrix.py:870-871 把這兩欄列為忽略）
  - backend/app/backtest/engine.py:1842-1874 只有「一字價且鎖死」才擋買賣
  - docs/taiwan-market-contract-v1.md:29「A limit-up is the greatest valid tick not above the raw ceiling; limit-down is the least valid tick not below the raw floor」
  - CONTRIBUTING.md:157 要求保持「漲跌停不可成交」約束
- **線上證據**：
  - https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html 「漲停價往下找最接近、且不超過 10% 上限的合法價格」；例：「基準價 40.60 元計算，理論漲停為 44.66 元、跌停為 36.54 元…合法價格要調整為 44.65 與 36.55 元」
  - 官方參考資料 TWT49U（證交所除權息計算結果表，reference\csv\twse_twt49u_20260601_20260923.csv）：2330 2026-06-11 基準 2248.99 → 官方漲停／跌停 2470／2025（app 算 2473.89／2024.09）；3034 2026-07-10 基準 519 → 官方 570／467.5（app 570.90／467.10）
- **本地計算證據**：
  - 指令：uv run python scratchpad\audit\work\A11\verify_pricelimitmodel.py：一般股 781 筆，backtest numpy_limit_price 完全相符只有 52 筆；app 漲停價高於官方 697 筆、跌停價低於官方 674 筆。台股 PriceLimitModel 相符 758 筆；ETF 120 筆中 numpy 相符 59 筆、PriceLimitModel 120 筆全對
  - 指令：uv run python scratchpad\audit\work\A11\verify_limits_adj.py Part 2：9/23 官方 MI_INDEX 有 16 檔一般股以漲跌停價收盤；對同日 enriched 資料呼叫 _limit_lock_matrices，只偵測到 2 檔（4770、6243，因為取整剛好落在跳動單位上）。6225.TWSE 開高低收＝46.2（一字跌停），app 算的跌停價 46.17，backtest_lock=False，也就是回測允許當天賣出；同一批 enriched 的 signal_limit_down=True（台股管線判斷正確）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：正式回測的 parquet 矩陣路徑（matrix_native／composite 策略；matrix.py:1014、1174 → _limit_lock_matrices）對台股一律用 numpy_limit_price 算漲跌停價，做法是 A 股的「四捨五入到 0.01」。台股規則是依升降單位取整：漲停往下取、跌停往上取。永豐金 Shioaji 文件的 2330 範例（2440 → 2680／2200）與交易所 TPEx、TWT49U、MIS 官方數據都證實這一點。因此 app 算出的漲停價恆 ≥ 官方、跌停價恆 ≤ 官方，只會漏判：
- 2024-01～2026-09 共有一字漲停 2247 根，回測只擋 299 根；一字跌停 2324 根，只擋 498 根。
- 9/23 的 6225 一字跌停在回測中可以賣出。
- ETF 也受影響，例如 0050 官方 123.0／100.7，app 算出 123.04／100.67。
依 CONTRIBUTING 屬於 P1 錯誤金融口徑。在使用者目前的部署下，沒有前端頁面，mining 排程也關閉，只有直接呼叫 API 才會觸發。
修法應在 _limit_lock_matrices 對 .TWSE／.TPEX 改用 PriceLimitModel.calc_limits_for_pct／TickSizeModel（依 ETF／股票分類），並調升 _DIRECT_MATRIX_LOADER_VERSION 讓舊快取失效。「改讀 enriched signal_limit_*」這條替代方案不可行：只有 2026-09-10 以後的 10 個分區有這兩欄，而且除息日（例如 9/23 的 1442、2542）會誤標。
    - 理由：這條 finding 的每一項實質主張，我都親自讀程式碼並在本地重算過，結果一致。

一、程式碼機制成立，引用的行號沒有偏移：
- numpy_limit_price 以「分」為單位做四捨五入：cents=floor(prev×100+0.5)，結果＝(cents×110+50)//100/100。
- _limit_lock_matrices 對所有標的一律用這個函式算漲跌停價，判定式為 raw≥up−0.005 與 raw≤down+0.005。
- 兩條 parquet 矩陣路徑都會呼叫它：matrix.py:1014 與 matrix.py:1174（磁碟快取版）。
- matrix_native／composite 策略經由 engine.py:544 load_market_data_matrix_from_parquet 進入。
- 撮合端（engine.py:1842-1874，以及 917-947 的另一份）要同時符合「一字價」與「lock flag」才擋買賣。
- 這個算法相當於「漲停價不低於原始上限減 0.005」。台股規則則要求「漲停價 ≤ 原始上限」，而且必須落在升降單位上。所以 app 算出的漲停價永遠 ≥ 官方、跌停價永遠 ≤ 官方，只會漏判，不會誤判。本地實測的誤判數是 0。

二、正確的規則（與 finding 所引 efrontrade 不同的來源）：
- 永豐金證券 Shioaji 官方 API 文件的合約範例：2330 reference=2440.0、limit_up=2680.0、limit_down=2200.0。依升降單位取整：2684 往下取到 5 元級距得 2680，2196 往上取得 2200。app 的公式會得出 2684.00／2196.00。
- 交易所自己公布的數據（本地參考資料）：
  - TPEx 9/23 的次日漲跌停價：888 檔一般股中，PriceLimitModel 888 檔全對，numpy 只對 130 檔。
  - TWT49U：772 檔中 PriceLimitModel 對 756 檔，numpy 只對 52 檔。
  - MIS：2454 參考價 5180，官方 5695／4665，numpy 算出 5698／4662；ETF 0050 參考價 111.85，官方 123.0／100.7，numpy 算出 123.04／100.67。
- 上述資料都證明規則是「漲停往下取、跌停往上取到合法升降單位」，與 repo 自己的 docs/taiwan-market-contract-v1.md:29 一致。
- 維基百科中文「漲停板」與「臺灣證券交易所」條目、MoneyDJ「漲跌停限制」條目都沒有取整規則的原文。WebSearch 額度已用盡，所以沒有找到第二份文字條文，這一點只由官方數據與券商 API 範例支撐。

三、實際漏判規模（本地資料 2024-01-02～2026-09-23，一般股，以 prev_close 套用升降單位規則）：
- 一字漲停 2247 根，回測只擋 299 根（13%）。
- 一字跌停 2324 根，回測只擋 498 根（21%）。
- 9/23 的 6225.TWSE 開高低收都是 46.2，是一字跌停：app 算的跌停價 46.17，回測判定不鎖死，也就是允許當天賣出。
- 9/23 真正收在漲跌停的 16 檔中，只偵測到 4770、6243 兩檔。另外被標成跌停的 1442、2542 是 9/23 除息（TWT49U 官方跌停價 25.9／37.35，收盤 27.8／39.55），屬於誤標，不是真的跌停。以上與 finding 所述完全吻合。

四、嚴重度：
- 這是回測撮合中「漲跌停不可成交」這條約束（CONTRIBUTING.md:157）的金融口徑錯誤，而且對台股幾乎等於失效，依第 11 節屬於 P1「錯誤金融口徑」，維持 P1。
- 在使用者目前的情境下，這個錯誤目前無法從 UI 觸發，只有直接呼叫 API 才會發生：
  - 前端 router 沒有回測頁；TaiwanStockDetail.tsx:452 註記已移除回測連結。
  - mining 排程預設關閉（preferences.py:539），使用者的 preferences.json 沒有任何 mining 設定，data/research/mining/runs 也是空的。
  - 這一點 finding 已自行揭露。

五、finding 沒提到、會影響修法的新面向：
- (a) 修法建議中的替代方案「直接讀 enriched 的 signal_limit_up／down」行不通：
  - kline_daily_enriched 共 661 個分區，只有 2026-09-10 起的 10 個分區有這兩欄；例如 2025-04-09、2026-09-09 分區根本沒有這個欄位。
  - enriched 在除息日也會誤標（1442、2542 在 9/23 的 signal_limit_down=True）。
  - 應該採用 TickSizeModel／PriceLimitModel 的方案。
- (b) 除息日的參考價：9/23 的 adj 因子尚未反映除息，回測以原始昨收當參考價，把 1442、2542 標成跌停鎖死。這兩檔不是一字價，所以當天不影響成交，但遇到一字價時就會誤擋賣出（推測）。
- (c) 磁碟矩陣快取會保存 limit_up_locked／limit_down_locked（matrix.py:1252-1253）。修正時要調升 _DIRECT_MATRIX_LOADER_VERSION，否則舊快取會繼續沿用錯誤的旗標。目前 data/.backtest_matrix_cache 只有 axes 索引檔，還沒有矩陣快取。
    - 證據：
      - backend/app/price_limits.py:242-259 numpy_limit_price：numerator=rint((1±pct)×100)、cents=floor(prev×100+0.5)、result=(cents×numerator+50)//100/100，沒有任何升降單位處理
      - backend/app/backtest/matrix.py:2167-2243 _limit_lock_matrices 以 numpy_limit_price 算 up_price／down_price，up_locked＝current_raw ≥ up_price−0.005（2233-2238）；呼叫點在 matrix.py:1014-1023（記憶體）與 1174-1185（磁碟快取）；matrix.py:861-873 把 signal_limit_up／down 列為忽略欄位
      - backend/app/backtest/engine.py:544 load_market_data_matrix_from_parquet 是 matrix_native 回測的入口；engine.py:1842-1874 與 917-947 的 _one_price_limit 要求 flag 為 True 且為一字價，_can_buy／_can_sell 才會擋
      - backend/app/taiwan/market_rules.py:217-236 TickSizeModel.round_price_limit_boundary（漲停 floor、跌停 ceil），以及 309-322 PriceLimitModel.calc_limits_for_pct：正確實作存在，但回測沒有使用
      - 線上（與 finding 不同的來源）：https://sinotrade.github.io/zh/tutor/contract/ 永豐金 Shioaji 文件範例：code='2330'、reference=2440.0、limit_up=2680.0、limit_down=2200.0（依升降單位 5 元取整；app 公式會得 2684.00／2196.00）
      - 線上查無原文：zh.wikipedia.org/zh-tw/漲停板（頁面只有 A 股內容）、zh.wikipedia.org/zh-tw/臺灣證券交易所（只寫「每日漲跌幅限制原則上為10%」）、MoneyDJ 漲跌停限制條目（講的是期貨），三者都沒有取整規則；WebSearch 額度已用盡
      - 本地指令：cd backend && uv run python scratchpad\audit\work\verify_limitlock_def\verify.py → [TPEx 9/23 次日漲跌停，4 碼股] n=888，numpy_both_match=130，PriceLimitModel_both_match=888，app_up>official=758，app_down<official=690；[TWT49U 4 碼股] n=772，numpy=52，PriceLimitModel=756；TWT49U 2330 2026-06-11 ex_ref=2248.99，官方 2470/2025，numpy 2473.89/2024.09；3034 2026-07-10 官方 570/467.5，numpy 570.90/467.10；MIS 2454 官方 5695/4665，numpy 5698/4662；MIS 0050（ETF）官方 123.0/100.7，numpy 123.04/100.67
      - 本地指令：uv run python ...\verify_limitlock_def\verify_hist.py（對 enriched 2024-01-02..2026-09-23 呼叫真正的 _limit_lock_matrices）→ 一字漲停 2247 根，backtest 擋 299；一字跌停 2324 根，backtest 擋 498；誤判鎖死 0；2025 年一字跌停 1989 根中只擋 425
      - 本地 9/23 對帳（d923.py＋官方 MI_INDEX）：6225.TWSE OHLC 皆 46.2、prev 51.3，numpy 跌停 46.17、升降單位跌停 46.2，bt_down=False；只有 4770（275.0）、6243（41.25）的 numpy 值剛好落在升降單位上才被偵測到；1442、2542 在 9/23 是除息日（TWT49U 官方跌停 25.9／37.35，收盤 27.8／39.55），enriched 與 backtest 都誤標成跌停
      - 新面向：enriched 只有 10/661 個分區有 signal_limit_up（2026-09-10 起；2025-04-09、2026-09-09 分區 has_col=False），所以「改讀 enriched 訊號」的修法對歷史回測無效
      - 使用者情境：frontend/src/router.tsx 沒有回測路由；frontend/src/pages/TaiwanStockDetail.tsx:452 註記已移除回測連結；backend/app/services/preferences.py:539 mining_schedule_enabled 預設 False，data/user_data/preferences.json 沒有 mining 鍵，data/research/mining/runs 為空 → 目前只有直接呼叫 /api/backtest/strategy/run 才會觸發
      - CONTRIBUTING.md:157「股票規則需保持 T+1、手續費、滑點、漲跌停不可成交等約束」；CONTRIBUTING.md:290 P1 包含「錯誤金融口徑」；docs/taiwan-market-contract-v1.md:29 明訂 limit-up 取不超過上限的最大合法升降單位
      - 驗證前後 git status --porcelain 相同（只有既有的 3 個 M 檔），沒有寫入 repo 或 data
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：原主張成立，數字全部重現。正式回測的 parquet 矩陣路徑（matrix.py:1014 → _limit_lock_matrices）每天都用 numpy_limit_price 算漲跌停價，做法是 0.01 四捨五入，不依台股升降單位取整。最後一日的 latest_limits 覆蓋在台股部署下不會生效，因為 data/instruments 是空目錄。對照官方資料：TWT49U 一般股 781 筆只有 52 筆相符；TPEx 9/23 次日漲跌停 888 檔只有 130 檔相符；MIS 9/23 的 2454 官方 5695／4665，app 算 5698／4662。9/23 以漲跌停收盤的 16 檔只鎖住 2 檔。

實際影響成交的是一字 bar，因為 engine 只擋「一字且鎖死」：9/23 真正影響成交的漏判是 6225，一字跌停卻允許賣出。全部歷史中，一字漲停 2243 根只鎖住 295 根，一字跌停 2311 根只鎖住 486 根。自選股 2454 在 2026-09-01 一字漲停 4315，回測仍允許買進。

修正時要注意：「改讀 enriched 的 signal_limit_up／down」不夠用，因為 661 個分區中只有 2026-09-10 以後的 10 個分區有這兩欄。應在 _limit_lock_matrices 對 .TWSE／.TPEX 改用 TickSizeModel.round_price_limit_boundary（區分股票與 ETF 的 tick class）來算漲跌停價，並用 TWT49U／MIS 的官方漲跌停價做測試 fixture。
    - 理由：我自己寫了兩支腳本，用官方參考資料（TWT49U、TPEx 9/23 次日漲跌停、MIS 9/23 的 u/w、MI_INDEX 9/23、STOCK_DAY）加上本地 kline_daily_enriched 重算，並直接呼叫正式的 load_market_data_matrix_from_parquet（cache_root=None，只在記憶體建矩陣）。finding 列出的數字全部逐一重現。

程式碼路徑也確認如 finding 所述：
- price_limits.py:256-258 用 0.01 四捨五入算價。
- matrix.py:2224-2225 呼叫它，2234／2237 做 ±0.005 比較。
- matrix.py:861-871 把 signal_limit_up／down 列為忽略欄位。
- engine.py:1842-1854 只擋一字價且鎖死的 bar。

另外查到四點，原 finding 沒有寫到：
(1) 最後一個分區雖然會用 instruments 的 limit_up／down 覆蓋（apply_latest_limits），但台股部署的 data/instruments 是空目錄，實際上不會覆蓋，每天都走 numpy_limit_price。
(2) 實際影響交易的是「一字漲跌停」。9/23 的 16 檔中只有 6225 是一字 bar，所以當天影響成交的漏判是 1 檔，不是 14 檔。放到全部歷史（2024-01-02～2026-09-23），用升降單位規則從前一日 raw_close 推算：一字漲停 bar 2243 根，回測只鎖住 295 根；一字跌停 bar 2311 根，只鎖住 486 根。自選股 2454 在 2026-09-01 是一字漲停 4315（官方 STOCK_DAY 開高低收都是 4315，前收 3925），app 算的漲停價是 4317.5，沒有鎖住。
(3) 修正建議的另一條路「讀 enriched 的 signal_limit_up／down」不夠用：661 個分區中只有 2026-09-10 以後的 10 個分區有這兩欄，歷史回測等於完全沒有鎖死判定。
(4) 回測前端路由已經移除（frontend/src/lib/navigation.ts:25-30），「只有直接呼叫 API 才會觸及」這一點成立。嚴重度維持 P1：這是錯誤的交易規則與金融口徑，但沒有前端入口，也不會破壞資料。
    - 證據：
      - 腳本：AUDIT_SCRATCH/work/verify-limlock/verify.py（輸出 partA_twt49u_compare.csv、partB_tpex_compare.csv、partC_0923_locks.csv）；執行後 git status 與執行前相同，data\ 下沒有新檔（security master 另外複製到 scratch 讀，網路與存檔方法都已封鎖）
      - TWT49U（base＝ex_ref_price_twd，排除 9999.95 無漲跌幅）一般股 781 筆：numpy 兩邊都相符 52 筆、漲停高於官方 697、跌停低於官方 674、PriceLimitModel 相符 758；ETF 120 筆：numpy 相符 59、PriceLimitModel 120。與 finding 完全一致（finding：781／52／697／674／758；ETF 120／59／120）
      - TWT49U 個例並列：2330 2026-06-11 base 2248.99 官方 2470／2025，app 2473.89／2024.09（finding 相同）；3034 2026-07-10 base 519 官方 570／467.5，app 570.9／467.1（finding 相同）；另補 2330 2026-09-16 base 2377.99 官方 2615／2145，app 2615.79／2140.19
      - finding 沒有用到的獨立來源一：TPEx dailyQuotes 9/23 次日漲跌停，股票 888 檔，numpy 兩邊都相符只有 130 檔（漲停偏高 758、跌停偏低 690），PriceLimitModel 888 檔全對
      - finding 沒有用到的獨立來源二：MIS 9/23 u/w：2454 ref 5180 官方 5695／4665，app 5698／4662；6488.TPEX ref 971 官方 1065／874，app 1068.1／873.9；0050 ref 111.85 官方 123.0／100.7，app 123.04／100.67
      - MI_INDEX 9/23：一般股以官方漲跌停價收盤 16 檔（漲停 15、跌停 1、一字 1）；正式 load_market_data_matrix_from_parquet（2026-09-10～09-23）只鎖住 4770、6243 兩檔（2/16）。6225.TWSE：ref 51.3、開高低收都是 46.2，官方跌停 46.2、app 46.17、backtest_lock=False、engine 的 _one_price_limit 判定結果為 False，enriched signal_limit_down=True。與 finding 一致
      - 全部歷史（matrix 661×2346，history.py）：用升降單位從前一日 raw_close 推算，一般股一字漲停 bar 2243 根、回測鎖住 295；一字跌停 bar 2311 根、鎖住 486；所有收在漲停 23041 根、鎖住 3051
      - 自選股個例：2454 在 2026-09-01，官方 STOCK_DAY（reference csv）開高低收＝4315／4315／4315／4315、漲跌 +390（前收 3925），app 漲停 4317.5，lock_up=False；2454、3034、3545 在 2025-04-07 的一字跌停也都沒有鎖住
      - backend/app/backtest/matrix.py:2226-2231 最後一個分區才用 latest_limits 覆蓋。latest_limits 來自 instruments 的 limit_up／limit_down（matrix.py:2116-2155），而 instruments 來自 repo.get_instruments_asset（engine.py:510 → repository.py:1307、365），讀的是 data\instruments\**\*.parquet，實測是空目錄，所以台股不會覆蓋
      - kline_daily_enriched 共 661 個分區，只有 date=2026-09-10～2026-09-23 這 10 個分區有 signal_limit_up／down 欄位（逐檔 read_parquet_schema 檢查）
      - backend/app/backtest/engine.py:1842-1854 _one_price_limit 同時要求 flag 為真且開高低收相同；1856-1874 _can_buy／_can_sell 只在這個條件下擋單
      - docs/taiwan-market-contract-v1.md:29「A limit-up is the greatest valid tick not above the raw ceiling; limit-down is the least valid tick not below the raw floor.」；CONTRIBUTING.md:157「漲跌停不可成交等約束」
      - 線上：https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html 原文「漲停價要往下找最接近、且不超過 10% 限制的合法價格。跌停價要往上找最接近、且不低於 10% 下限的合法價格。」例：基準 40.60 → 理論 44.66／36.54 → 合法 44.65／36.55（WebFetch 實抓）
      - frontend/src/lib/navigation.ts:25-30 註明 /backtest 的 route 與 component 已刪除，backend /api/backtest 沒有前端消費端；router.tsx 沒有 mining／backtest 路由

### [P1 → P1] 台股回測使用未還原價格且不計股利，除權息缺口被當成虧損，並誤觸停損（確認成立）

- **主張**：台股 enriched 的 open/high/low/close 是原始價：Hybrid provider 的 get_adj_factors 回空表，註解是「Phase 2 records raw price semantics」。回測模組完全不處理股利（grep dividend、ex_factor 在 backend/app/backtest 下無結果）。因此持有期間跨越除息日時，報酬少算整筆股利，停損與移動停損也會被除息缺口觸發。這與 CONTRIBUTING 所寫的「enriched 為前復權價格」不符。
- **正確的定義或數值**：回測報酬應為含息總報酬：使用還原價，或在除息日把股利以現金入帳；停損與均線等訊號也應以還原價判斷，避免把除息缺口當成價格下跌。
- **app 實際行為**：用原始價撮合、股利不入帳，除息日當成真實下跌。
- **對使用者的影響**：自選股中高股息 ETF 00878（每季配息約 3%）、0050，以及 2379、3034、2454 等配息股的回測，會系統性低估報酬。停損型策略在除息日集中出現假停損。影響目前只限於直接呼叫 API 的回測。
- **修法建議**：台股接入 TWT49U 除權息參考價，產生 ex_factor（官方 pre_ex_close／ex_ref 比值），走既有 _apply_adj_factor 的前復權路徑；或在撮合端依除息事件把現金股利入帳，並修正 CONTRIBUTING 3.2 對台股的描述。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/hybrid_provider.py:144-152 get_adj_factors 回傳 pl.DataFrame()，docstring 為 'Return empty adjustment factors (Phase 2 records raw price semantics)'
  - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」
  - backend/app/backtest/engine.py:1906-1909 出場金額只算價差，沒有股利
  - backend/app/backtest/engine.py:2008-2026 停損線以進場價的百分比計算
- **線上證據**：
  - https://www.tejwin.com/insight/%E9%82%84%E5%8E%9F%E8%82%A1%E5%83%B9/ 還原股價定義為「將股票歷年來發生的配息、配股、減資、增資等會影響原股東權益的事件納入考量後，所回推出的歷史股價」；並指出若用未調整股價回測，將會「忽略除權息與增減資等事件，可能出現『錯誤的進出場判斷』或『績效過度失真』」
  - 官方 TWT49U：00878 除息日 2026-08-18，除息前收盤 33.75，參考價 32.74，配息 1.01；2379 除息日 2026-09-08，729 → 704，配息 25.0
- **本地計算證據**：
  - 指令：uv run python scratchpad\audit\work\A11\verify_limits_adj.py Part 3：00878 8/17 enriched close=33.75、raw_close=33.75、官方 33.75；8/18 三者都是 32.38；2379 9/07 三者都是 729，9/08 三者都是 692。除息前後都沒有還原
  - 指令：uv run python scratchpad\audit\work\A11\verify_exdiv.py：2379 在 9/04 收盤買、9/11 收盤賣。未還原價的回測報酬 −1.2623%，含息實際報酬 +2.2440%；加上 3% 停損時，回測在 9/08 以 691.61 停損出場（−3.00%），還原價下不會觸發。00878 在 8/14 買、8/21 賣：回測 −4.3992%，含息實際 −1.4172%；加上 3% 停損時在 8/18 開盤 32.76 停損，還原價下不會觸發
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：台股 enriched 的 open/high/low/close 全部是未還原的原始價：全庫 1,423,597 筆台股資料都是 close=raw_close。直接成因是 backend\app\taiwan\enriched_refresh.py:95 寫死 factors=pl.DataFrame()；hybrid_provider.get_adj_factors 回空表只是旁證，台股 enriched 路徑根本不會呼叫它。回測模組（backend\app\backtest）完全沒有股利或除權息處理，進出場與停損都直接用 enriched 價格（engine.py:848-849、1904-1906、2008-2024）。因此跨越除息日持有時，回測報的是 price return 而非 total return，整筆股利會被少算。停損與移動停損會被除息缺口提早或錯誤觸發：以官方資料重算，2379 在 9/04 到 9/11 為 −1.26% 對比含息 +2.24%，00878 在 8/14 到 8/21 為 −4.40% 對比含息 −1.42%；3% 停損在原始價下觸發，前復權價下不觸發。這違反 CONTRIBUTING.md:100 的前復權契約，屬於錯誤金融口徑。但前端已移除 /backtest（navigation.ts:25-30），使用者也沒有直接呼叫 /api/backtest，目前是潛伏缺陷，實際影響為零，所以列 P2；重新開放回測功能前必須先修，屆時應視為 P1。修法須改 enriched_refresh.py:95 的因子來源（例如由 TWT49U 的 pre_ex_close/ex_ref 產生 ex_factor），不能只改 provider。
    - 理由：Finding 的技術主張全部成立，我逐項實查過。(1) 台股 enriched 確實是原始價：我掃了 data\kline_daily_enriched 全部 661 個分區（2024-01-02 到 2026-09-23），1,423,597 筆台股資料中 close≠raw_close 的筆數為 0。00878 在 8/17、8/18，2379 在 9/07、9/08，0050 在 7/20、7/21 的 enriched close 都等於官方 STOCK_DAY 的原始收盤價。(2) 回測模組完全沒有股利處理：backend\app\backtest 下 grep dividend、ex_factor、adj_factor、除息、除權、股利、配息，命中 0 筆。進出場價直接取 matrix.open 或 matrix.close（engine.py:848-849）；_sell 的出場金額只算價差（engine.py:1904-1906）；停損線是進場價乘以 (1−pct)，開盤價或最低價跌破就出場（engine.py:2008-2026）。(3) 我用官方 TWT49U 與 STOCK_DAY 參考資料獨立重算，數字與 finding 一致。2379 在 9/04 收盤買進、9/11 收盤賣出：原始價報酬 −1.2623%，含息報酬 (704+25)/713−1 = +2.2440%。設 3% 停損時，原始價在 9/08 以 691.61 出場，前復權價不會觸發。00878 在 8/14 買、8/21 賣：原始價 −4.3992%，含息 −1.4172%。設 3% 停損時，原始價在 8/18 以開盤價 32.76 出場，前復權價不會觸發。(4) 與 CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」矛盾，而且 docs 裡找不到任何地方說明台股例外。(5) 定義面：Wikipedia 的 Total return 條目區分含股利的 total return 與只算資本利得的 price return。Ex-dividend date 條目指出除息日價格預期下跌股利金額，券商還會把停損單同步下調股利金額。官方 TWT49U 也顯示參考價等於除息前收盤減股利（33.75−1.01=32.74；729−25=704）。所以原始價回測會把除息缺口當成虧損，也會觸發停損。需要修正的有兩點。第一，直接成因的程式碼位置：台股 enriched 並沒有呼叫 hybrid_provider.get_adj_factors；整個 app 裡只有 A 股的 kline_sync.py:344 會呼叫它。真正把因子設為空的是 enriched_refresh.py:95 寫死的 compute_enriched(raw, factors=pl.DataFrame(), ...)。只讓 provider 回傳因子修不好，必須改 enriched_refresh。第二，嚴重度：依類型屬於「錯誤金融口徑」，本可列 P1，但前端已移除 /backtest 路由（navigation.ts:25-30 註明「/api/backtest 仍註冊但無前端消費端」，router.test.ts:20-22 斷言不註冊 backtest），使用者情境中也沒有直接呼叫 API，目前實際影響為零，屬於潛伏缺陷。因此我建議降為 P2；重新開放回測前必須先修，屆時回到 P1。另外，均線、MACD 等指標也用原始價計算，會讓監控與雷達訊號受除息缺口影響。這不在本 finding 的回測範圍，我也沒有查證使用者畫面是否受影響（推測）。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 `enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`：台股 enriched 寫死空的復權因子，這才是直接成因
      - backend/app/taiwan/providers/hybrid_provider.py:144-152 get_adj_factors 回傳 pl.DataFrame()，docstring 為 'Phase 2 records raw price semantics'；但 grep 顯示整個 app 只有 app\services\kline_sync.py:344（A 股同步）呼叫 get_adj_factors，台股 enriched 路徑不經過它
      - backend/app/indicators/pipeline.py:1015-1024 先把原始價複製成 raw_close/raw_high/raw_low，只有 factors 非空才呼叫 _apply_adj_factor
      - CONTRIBUTING.md:100「enriched 的 `open/high/low/close` 為前復權價格。」；docs\ 下沒有任何文件說明台股例外（grep raw/adjust/復權 無相關結果）
      - backend/app/backtest/engine.py:848-849 進出場價取 matrix.open/matrix.close；engine.py:1904-1906 `exit_value = pos["shares"] * exit_price * (1 - sell_cost_pct)`，沒有股利；engine.py:2008-2024 停損線 = entry_price*(1-|stop_loss_pct|)，開盤價≤停損線以開盤價出場，最低價≤停損線以停損價出場
      - 指令 grep -rn -i -E "dividend|ex_factor|adj_factor|除息|除權|股利|配息" app/backtest --include=*.py | wc -l → 0
      - 本地掃描 scratchpad\audit\work\V_bt_adj\scan_all.py → `partition files=661 rows=1423597 taiwan_rows=1423597 taiwan_rows_close_ne_raw=0 dates 2024-01-02..2026-09-23`
      - 本地讀取 scratchpad\audit\work\V_bt_adj\read_enriched.py：00878 8/17 close=raw_close=33.75、8/18=32.38；2379 9/07=729、9/08=692；0050 7/20=99.2（等於官方 pre_ex_close 99.2）、7/21=102.5。除息前後都沒有還原
      - 官方參考資料 reference\csv\twse_twt49u_20260601_20260923.csv：00878 除息日 2026-08-18，pre_ex_close 33.75、ex_ref 32.74、股利 1.01；2379 除息日 2026-09-08，729 → 704、股利 25.0；0050 除息日 07-21，99.2 → 98.6、股利 0.6；2454 除息日 07-07，股利 24.5；3034 除息日 07-10，股利 23.0；3545 除息日 08-04；6196 除息日 06-24
      - 本地重算 scratchpad\audit\work\V_bt_adj\recompute.py（官方 STOCK_DAY＋TWT49U）：2379 原始價 ret=-1.2623%，3% 停損在 ('2026-09-08', 691.61)；前復權 ret=2.2440%，stop_exit=None；含息 (704+25)/713-1=2.2440%。00878 原始價 -4.3992%，停損在 ('2026-08-18', 32.76)；前復權 -1.4500%，stop_exit=None；含息 -1.4172%。另外 5% 移動停損在原始價下 9/08 出場，前復權下延後到 9/10
      - https://en.wikipedia.org/wiki/Total_return："The total return on a portfolio of investments takes into account both the capital appreciation on the portfolio, and the income received on the portfolio." / "This contrasts with the price return, which takes into account only the capital gain on an investment."
      - https://en.wikipedia.org/wiki/Ex-dividend_date："The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend"；除息前 "all open good-until-canceled limit, stop, and stop limit orders are automatically reduced by the amount of the dividend"（實務上停損單會隨股利下調，不會被除息缺口觸發）
      - https://zh.wikipedia.org/zh-tw/除權：除權參考價 = 前收盤 ÷ (1+配股率)，並有填權／貼權的定義，確認除權息缺口是參考價調整，不是真實跌價
      - frontend/src/lib/navigation.ts:25-30「/backtest 移出 CORE_NAV … backend 的 /api/backtest 仍註冊但無前端消費端」；frontend\src\router.test.ts:20-22 斷言不註冊 backtest 路由：使用者情境下無法從 UI 觸發
      - backend/app/backtest/engine.py:508 回測讀 data_dir/enriched_dirname(asset_type)，也就是 TaiwanEnrichedRefreshService 寫入的 kline_daily_enriched（enriched_refresh.py:28-34）；matrix.py:4012 有台股專屬分支，表示回測本來就設計成可跑台股
      - 官方來源 TEJ（finding 原引）未再抓取；Investopedia 抓取失敗（WebFetch 回報 unable to fetch）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 的 open/high/low/close 實際是原始價：data/adj_factor 是空的，Hybrid provider 的 get_adj_factors 也回傳空表。這與 CONTRIBUTING.md:100 寫的「前復權」不符。回測撮合器不處理股利，出場只算價差。因此持有期間跨越除息日時，報酬會少算整筆股利：2379 在 9/04 買、9/11 賣，回測 −1.2623%，含息實際 +2.2440%；00878 在 8/14 買、8/21 賣，回測 −4.3992%，含息實際 −1.4172%。固定停損也會被除息缺口誤觸：2379 設 3% 停損，在 9/08 以 691.61 出場（−3.00%）；00878 設 3% 停損，在 8/18 開盤以 32.76 出場（−3.28%）；00878 設 5% 停損，在 8/19 出場。以上情形在還原價下都不會觸發。移動停損也可能被誤觸：00878 設 4% 移動停損時，只有原始價會在 8/18 觸發。但不是每筆都如此：2379 在 9/08 的盤中下跌是真實的，還原價下同樣觸發。所有撮合路徑（portfolio、independent、legacy）的停損邏輯相同，都受影響。影響目前只限於直接呼叫 /api/backtest 或 mining API，前端沒有回測入口。
    - 理由：我用本地 parquet 和官方 TWT49U／STOCK_DAY 參考資料重算，並把本地 enriched 資料餵進正式撮合器 BacktestEngine.simulate_portfolio（它與 API 用的 simulate_market_matrix 共用 _simulate_portfolio_matrix）。finding 的每個數字都完全重現。
(1) 台股 enriched 的 open/high/low/close 就是原始價：2379 在 9/07 與 9/08、00878 在 8/17 與 8/18、0050 在 7/20 與 7/21，close 都等於 raw_close，也等於官方收盤。data/adj_factor 目錄是空的（0 個檔），所以 pipeline.py:1078 讀不到任何因子。
(2) backend/app/backtest 下 grep dividend|ex_factor|adj_factor|除息|除權 沒有結果。
(3) 以下並列 finding 的數字與我的重算（撮合器，成本設 0）：
- 2379 9/04 收盤買、9/11 收盤賣，未還原：finding −1.2623%，我算 −1.2623%。
- 同一筆含息：finding +2.2440%，我算 +2.2440%（依官方 9/08 除權息參考價 704/729 前復權後跑撮合器，也是 +2.244%）。
- 2379 加 3% 停損：finding 為 9/08 以 691.61 停損、−3.00%；我算 ('2026-09-08', 691.61, −3.0, stop_loss)。還原價下不觸發，持有到 9/11 以訊號出場，+2.244%。
- 00878 8/14 買、8/21 賣，未還原：finding −4.3992%，我算 −4.3992%。
- 同一筆含息（現金加回）：finding −1.4172%，我算 −1.4172%；改用前復權價跑撮合器是 −1.45%，差異只來自比例還原與現金加回兩種算法。
- 00878 加 3% 停損：finding 為 8/18 開盤 32.76；我算 ('2026-08-18', 32.76, −3.2772%, stop_loss)。還原價下的停損線是 31.8707，期間最低 31.92，不觸發。
(4) 另外找到兩個 finding 沒提的面向，都支持結論：00878 設 5% 停損時，原始價在 8/19 以 32.03 停損（−5.43%），還原價下不觸發；設 4% 移動停損時，原始價在 8/18 觸發，還原價下不觸發。反例：2379 設 3%／4% 移動停損時，9/08 在兩種價格下都會觸發。當天盤中最低 680，比參考價 704 低 3.4%，是真實下跌。所以「移動停損被除息誤觸」不是每筆都成立，但原始價算出的 pnl 仍少了 25 元股利。
(5) 程式碼證據都成立：CONTRIBUTING.md:100 寫「enriched 為前復權價格」；hybrid_provider.py:144-152 回傳空表；engine.py:1906-1908 只算價差；engine.py:2009／2011／2023 的停損線依進場價與峰值計算。同樣的邏輯也在 independent 路徑 engine.py:1380-1398 與 legacy 路徑 engine.py:959-972、2552-2574。
(6) TEJ 頁面的原文經 WebFetch 核實無誤。
(7) 影響範圍與 finding 一致，只限於 API：frontend/src/lib/navigation.ts:25-30 寫明 /backtest 路由已刪、/api/backtest 沒有前端使用端；mining 的 startMining 也沒有任何頁面呼叫。docs/code-review-2026-09-22.md 裡沒有除息或復權相關記錄，不是重複回報。
嚴重度：這是 CONTRIBUTING 第 11 節定義的「錯誤金融口徑」，維持 P1。但依使用者情境，前端目前無法觸及回測，實際影響只在直接呼叫 API 的情況。
    - 證據：
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-exdiv-bt/p1_enriched.py → enriched 2379.TWSE 9/07 close=729.0 raw_close=729.0、9/08 open/high/low/close=717/721/680/692 raw_close=692；00878.TWSE 8/17 close=33.75 raw_close=33.75、8/18 32.76/32.76/32.3/32.38；0050 7/20 close=99.2（官方 pre_ex_close 99.2）；與官方 STOCK_DAY CSV 完全相同
      - 官方 TWT49U（reference/csv/twse_twt49u_20260601_20260923.csv）：2379 ex_date 2026-09-08 pre_ex_close 729.0 ex_ref 704.0 配息 25.0；00878 ex_date 2026-08-18 33.75→32.74 配息 1.01；0050 2026-07-21 99.2→98.6 配息 0.6
      - 指令：uv run python ...\verify-exdiv-bt\p2_engine.py（正式撮合器 simulate_portfolio、close_t、成本 0）→ 2379 RAW 無風控 ('2026-09-04',713.0,'2026-09-11',704.0,-1.2623,'signal')；ADJ (688.5487→704.0, +2.244)；RAW stop_loss 3% ('2026-09-08',691.61,-3.0,'stop_loss')；ADJ stop_loss 3% 不觸發，+2.244 signal
      - 同腳本 → 00878 RAW 無風控 -4.3992；手算含息 -1.4172；ADJ 撮合 -1.45；RAW stop_loss 3% ('2026-08-18',32.76,-3.2772,'stop_loss')；ADJ 停損線 31.8707 不觸發；RAW stop_loss 5% ('2026-08-19',32.03,-5.4325)，ADJ 不觸發；RAW trailing 4% 在 8/18 觸發，ADJ 不觸發
      - 反例（同腳本）：2379 trailing_stop 3% 在 RAW 與 ADJ 都於 2026-09-08 觸發，pnl 都是 +0.4011%（9/08 盤中最低 680，比參考價 704 低 3.4%，是真實下跌）
      - Grep 'dividend|ex_factor|adj_factor|除息|除權' 於 backend/app/backtest → No matches found；data/adj_factor 內 0 個檔案
      - backend/app/taiwan/providers/hybrid_provider.py:144-152 get_adj_factors 回傳 pl.DataFrame()，docstring 'Return empty adjustment factors (Phase 2 records raw price semantics)'
      - CONTRIBUTING.md:100「enriched 的 `open/high/low/close` 為前復權價格。」
      - backend/app/backtest/engine.py:1906-1908 exit_value = shares*exit_price*(1-sell_cost_pct)、pnl_amount = exit_value - entry_value，沒有股利；engine.py:2009/2011/2023 停損線以 entry_price／peak_price 計算；相同邏輯另見 engine.py:1380-1398（independent）與 959-972、2552-2574（legacy）
      - https://www.tejwin.com/insight/%E9%82%84%E5%8E%9F%E8%82%A1%E5%83%B9/ 原文：「若使用未調整股價進行回測，將忽略除權息與增減資等事件，可能出現「錯誤的進出場判斷」或「績效過度失真」等錯誤結果。」
      - frontend/src/lib/navigation.ts:25-30 寫明 /backtest 路由已刪、「backend 的 /api/backtest 仍註冊但無前端消費端」；frontend\src\lib\miningTask.ts:236 startMining 在任何頁面都沒有呼叫者（grep 只命中 api.ts 與 miningTask.ts）

### [P1 → P1] minute_fill 的參考線（MA5）含成交當日收盤，形成未來函數（確認成立）

- **主張**：開啟 minute_fill 時，策略回測把 reference_price 設為 rolling_mean(close,5)，而這個值包含當列（成交當日）的收盤價。_resolve_minute_fill 買進時以「分鐘高點觸及參考線就用參考線成交」決定價格，所以成交價取決於盤中還不可知的當日收盤。
- **正確的定義或數值**：盤中的觸價線只能用前一交易日以前的資料，例如 sum4/4（minute_trigger.py 的賣出觸發線已經這樣做），或直接改用前一日的 MA。
- **app 實際行為**：用含當日收盤的 MA 當作盤中成交線。
- **對使用者的影響**：minute_fill 預設關閉，只能從 API 開啟；分鐘資料目前也只有 4 個交易日，實際影響極小。但一旦分鐘資料累積，精確成交的績效會帶有未來資訊。
- **修法建議**：reference_price 改用 shift(1) 後的 MA，或比照 build_minute_exit_reference 推導當日可知的觸價線。補一個「成交價不隨當日收盤改變」的測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/strategy.py:1369-1373 reference_price = rolling_mean(market_data.close, 5)[start_id:stop_id]（另見 948-949、1265-1269）
  - backend/app/backtest/engine.py:1814-1827 _refill_price 取 matrix.reference_price[time_id]（time_id 即成交日）
  - backend/app/backtest/engine.py:1594-1599 買進時高點 ≥ ref 就回傳 ref
  - backend/app/backtest/matrix.py:2357-2362 未給 reference_price 時，同樣取同列的 ma5／ma10／ma20
  - CONTRIBUTING.md:156「嚴禁未來函數：交易日只能使用當時已經公開且可獲得的數據」
- **線上證據**：
  - 無（屬程式邏輯問題；權威依據為專案 CONTRIBUTING 5.3 的未來函數禁令）
- **本地計算證據**：
  - uv run python -c 'rolling_mean([1..6],5)' 輸出 [nan,nan,nan,nan,3.0,4.0]：第 5 列包含自身
  - 指令：uv run python scratchpad\audit\work\A11\verify_minute_lookahead.py：成交日 MA5=104.0（含當日收盤 120），盤中可知的參考線（前 4 日均價）為 100.0；回測成交價 104.0，也就是用到了 13:30 才知道的收盤價
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：開啟 minute_fill 時，所有分鐘精確成交路徑的盤中參考線都是含成交日收盤的 MA5（或同列的 ma5／ma10／ma20），出現在：
- strategy.py:948-952、1265-1269、1369-1373
- matrix.py:2357-2362
- engine.py:893-903、1246-1265、2325-2347

engine.py:1594-1599 依這條線決定買在開盤價、參考線價或收盤價，所以成交價取決於盤中還不可知的當日收盤。實測兩個情境的早盤完全相同、只有尾盤不同，買價分別是 104.0 與 100.5；盤中可知的正確觸價線是前 4 日收盤平均 100，兩者都應該成交在 100.5。

偏誤方向不固定：本例反而讓買價變差，所以這是成交價失真，不能說成績效必然被高估。

另外，close_t 加 minute_fill 本身就有未來資訊：訊號要到收盤才成立，卻在盤中成交。只把參考線改成 shift(1) 或 S4/4，無法消除這一層。

使用者情境下，這個功能預設關閉、前端沒有入口，本地分鐘資料也只有 4 天，實際影響極小；但依 CONTRIBUTING:295，未來函數屬 P1。
    - 理由：這條 finding 成立，嚴重度維持 P1。

**程式碼（行號與現況一致）**
- 開啟 minute_fill 時，參考線 reference_price 都是 rolling_mean(market_data.close, 5)，出現在 strategy.py 三處：948-952、1265-1269、1369-1373。
- 這個均線的窗口包含當列：matrix.py:3032 的 rolling_mean 用 _rolling_reduce（matrix.py:3436 起），以 sliding_window_view 計算。
- 沒有傳入 reference_price 時，matrix.py:2357-2362 改取同列的 ma5／ma10／ma20，一樣含當日收盤。
- 撮合時 engine.py:1821 取 matrix.reference_price[time_id]。time_id 是成交日：買進在 engine.py:2105，賣出在 1903。open_t+1 模式下，訊號已先延後一根 K 棒，所以 time_id 是 T+1，參考線含 T+1 的收盤價。
- engine.py:1594-1599 的規則：開盤價 ≥ 參考線就以開盤價成交；否則只要全日任一分鐘高點 ≥ 參考線，就以參考線價成交。
- finding 沒提到的另外三條路徑也是同一個問題：
  - engine.py:893-903 另一個 matrix 撮合函式
  - engine.py:1246-1265 舊的 panel 路徑，取同列 ma5
  - engine.py:2325-2347 同上

**正確定義**
- QuantStart 對 look-ahead bias 的定義：模擬到某個時點時，用了當時實際上還拿不到的未來資料。
- TradingView 文件指出：盤中未收完的 K 棒，高、低、收都還會變動，要等收盤才確定。
- 專案 CONTRIBUTING.md:156 明文禁止未來函數；295 把「未來函數」列為 P1。
- 盤中 MA5 的穿越條件：當下價格 p ≥ (前 4 日收盤合計 S4 + p)/5，化簡為 p ≥ S4/4。所以盤中當下就能知道的觸價線是 S4/4，不是收盤後才定案的 MA5。minute_trigger.py:34 的 (5*ma5 - close)/4 正是這個值，當日收盤在算式中互相抵消。

**本地實測**
- 我自己寫了腳本，兩個情境的日線歷史與早盤分鐘線完全相同，只有尾盤不同，所以成交日收盤一個是 120、一個是 95。
- 兩條撮合路徑（strategy 的 matrix 路徑、panel 的 ma5 路徑），在 open_t+1 與 close_t 兩種模式下，買進成交價都是 104.0 對 100.5。
- 不含未來資訊的觸價線是 100，兩個情境都應該以開盤價 100.5 成交。
- 成交價跟著當日收盤改變，就證明用到了盤中還不知道的資訊。

**補充面向（原 finding 沒有寫）**
1. 偏誤方向不固定：本例的未來資訊反而讓買價變差（104 > 100.5）。所以應該說「成交價不可信」，不是「績效必然被高估」。
2. close_t 模式本身還有一層問題：訊號要到 T 日收盤才成立，卻在盤中成交。即使參考線改成 S4/4，只要訊號不是能在盤中重播的價格穿越型訊號，close_t 加 minute_fill 仍然帶有未來資訊。finding 的修法只解決參考線這一層。
3. 參考線固定用 MA5，不隨實際策略訊號而定，屬於口徑近似。

**使用者情境**
- minute_fill 預設 False（engine.py:74、api/backtest.py:345）。
- 前端沒有任何呼叫端送出這個參數：strategyBacktestRun 只定義在 api.ts:2708，沒有被使用；storage.ts:106 的 minuteFill 也沒有被使用。只能直接呼叫 API 開啟。
- 本地分鐘資料只有 2026-09-17、09-18、09-22、09-23 四天，只含自選 9 檔，每檔每日約 81 到 131 根，並非完整盤中。

**嚴重度**：實際影響極小，但依 CONTRIBUTING 第 11 節，未來函數明列為 P1，所以維持 P1。
    - 證據：
      - backend/app/backtest/strategy.py:948-952、1265-1269、1369-1373：reference_price = rolling_mean(market_data.close, 5)[start_id:stop_id]，只在 minute_fill 開啟時設定
      - backend/app/backtest/matrix.py:3032-3039 rolling_mean 呼叫 _rolling_reduce（3436 起，用 sliding_window_view），窗口包含當列
      - backend/app/backtest/matrix.py:2351-2362：沒有 reference_price 時，取同列的 ma5／ma10／ma20
      - backend/app/backtest/engine.py:1814-1827 _refill_price 取 matrix.reference_price[time_id, asset_id]；買進呼叫點在 2105-2107，賣出在 1903-1905，time_id 就是成交日
      - backend/app/backtest/engine.py:1594-1599 買進：開盤價 ≥ 參考線就用開盤價，否則全日任一分鐘高點 ≥ 參考線就用參考線價
      - 同樣的問題也在 finding 沒列的路徑：engine.py:893-903；engine.py:1246-1265 與 2325-2347（panel 路徑，取同列 ma5／ma10／ma20）
      - backend/app/backtest/minute_trigger.py:34 賣出觸發線 (5*ma5 - close)/4 = 前 4 日收盤平均，當日收盤在算式中抵消，所以盤中可知
      - CONTRIBUTING.md:156「嚴禁未來函數：交易日只能使用當時已經公開且可獲得的數據」；:295 把「未來函數」列為 P1
      - 線上：https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/ 原文 "Look-ahead bias is introduced into a backtesting system when future data is accidentally included at a point in the simulation where that data would not have actually been available."；同頁：高低點 "can only be calculated at the end of a time period, a look-ahead bias is introduced if these values are used -during- the current period"
      - 線上：https://www.tradingview.com/pine-script-docs/concepts/repainting/ 原文 "On realtime bars ... the high, low and close values are not fixed; they can change values many times before the realtime bar closes"
      - 本地：cd backend && uv run python scratchpad/audit/work/V_minute_lookahead/verify.py 輸出：盤中可知的觸價線 = 100.0；[open_t+1] A 情境（尾盤拉到收盤 120）ref=104.00，成交 @104.0；B 情境（尾盤殺到收盤 95）ref=99.00，成交 @100.5；[close_t] 結果相同；matrix 路徑與 panel 路徑一致。兩情境早盤分鐘線完全相同，成交價卻不同
      - 本地：data/kline_minute 只有 date=2026-09-17、09-18、09-22、09-23 四個分區，每個分區 9 檔（自選清單），筆數 909／1176／930／729
      - 前端 frontend/src/lib/api.ts:2708-2731 strategyBacktestRun 沒有任何呼叫端；storage.ts:106 minuteFill 沒有被使用；後端預設 minute_fill=False（engine.py:74、api/backtest.py:345）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：開啟 minute_fill 時，分鐘精確成交的參考線用的是含成交當日收盤的 MA5，形成未來函數。
- 矩陣策略：strategy.py 的 rolling_mean(close,5)。
- 非矩陣策略：matrix.py:2355-2362 取同列的 ma5/ma10/ma20。

_resolve_minute_fill 買進與賣出都依這條線決定用開盤價還是觸線價，所以成交價會隨 13:30 才確定的收盤改變。受影響的撮合路徑包括組合模式（engine.py:1814-1827）與獨立模式（engine.py:893-903）。

實測：分鐘K完全相同、只有收盤不同時，收盤 100/105/109.5/120 分別成交在 100/101/101.9/104；參考線改用 shift(1) 的 MA5 或 sum4/4 後，四種情境都固定成交在 100。以本地真實分鐘資料計算，36 個「代號-日」中，買進有 3 個、賣出有 7 個成交價不同，最大差 2.97%。

另外，entry_fill=close_t 時，訊號要到收盤才確認，minute_fill 卻給盤中價；即使把參考線改成因果版本，這部分仍是未來函數，要另外處理，例如 close_t 不允許搭配 minute_fill。

實際影響很小：minute_fill 預設關閉，前端沒有 UI，只能從 API 開啟；分鐘資料也只有自選 9 檔、4 個不完整的交易日。
    - 理由：我自己寫腳本重現，finding 的數值全部對得上，程式碼位置也都正確。另外找到三個原 finding 沒提到的面向。

一、程式碼鏈逐段核對
1. strategy.py:948-952、1265-1269、1369-1373 都是 `reference_price = rolling_mean(market_data.close, 5)[start_id:stop_id]`。
2. matrix.py:3032-3039 → `_rolling_reduce`（sliding_window_view 沿 axis=0，`out[window-1:] = mean`）：第 t 列是 t-4 到 t 的平均，包含當列。
3. build_market_matrix_from_signals（matrix.py:2351-2354）直接複製這份陣列，沒有位移。未提供時（非矩陣策略走 build_market_matrix，strategy.py 約 1503 行）改取同列的 ma5/ma10/ma20（matrix.py:2355-2362）。ma5 來自 pipeline.py:393 的 `rolling_mean(5)`，同樣包含當日。
4. engine.py:1814-1827 的 `_refill_price` 取 `reference_price[time_id]`。買進呼叫點在 2105-2107，time_id 就是成交日（date_text）。
5. engine.py:1594-1599 買進：開盤 ≥ ref 用開盤成交，否則分鐘高點 ≥ ref 用 ref 成交。

二、端到端重現
重現走的是正式撮合函式：simulate_market_matrix 與 simulate_portfolio → build_market_matrix。只把分鐘載入器換成合成分鐘K，程式不寫檔。
- 設定：前 5 日收盤都是 100；成交日開盤 99，第 2 根分鐘K高點 106；各情境只有最後一根分鐘K（收盤）不同。
- 目前寫法：收盤 100／105／109.5／120 對應的成交價是 100／101／101.9／104。收盤 120 時 MA5 = 104、成交價 104.0，和 finding 的 104.0 對 100.0 一字不差。
- 改用 shift(1) 的 MA5 或 sum4/4：四種情境的成交價都固定是 100。
- panel 的 ma5 fallback 路徑結果相同。
- rolling_mean([1..6],5) 輸出 [nan,nan,nan,nan,3.0,4.0]，和 finding 相同。

三、真實資料
- 本地日K收盤和官方 TWSE STOCK_DAY 比對 216 組（代號、日期），0 筆不符。
- 自選 9 檔 × 4 個分鐘日，共 36 個「代號-日」。以真實分鐘K比較目前的 MA5_t 與當日可知的 sum4/4：買進成交價有 3 個不同，賣出有 7 個不同，最大相對差 2.97%（6196.TWSE 2026-09-18 賣出：目前 523.0，當日可知線 539.0）。

四、線上佐證
Wikipedia 的 SMA 定義包含當期 p_n，所以日 MA5 要到收盤才確定。web search 額度已用完，Investopedia 抓取失敗。

五、補充面向（原記錄未提）
(a) 賣出也受影響：engine.py:1588-1593 同樣依 ref 決定成交價。
(b) 獨立模式也受影響：_simulate_independent_matrix（engine.py:893-903）也取 `reference_price[time_id]`。
(c) entry_fill=close_t 時，訊號本身要等收盤才確認，minute_fill 卻給盤中價（開盤價或觸線價）。即使參考線改成因果的 sum4/4，收盤 120 的情境仍以 100 成交（日K口徑是 120），還帶選樣偏差。所以 finding 建議的 shift 修法只能修好 open_t+1，close_t 要另外處理。

六、影響與嚴重度
- 影響範圍屬實：MatcherConfig.minute_fill 預設 False；api/backtest.py:345 預設 False；前端沒有任何 UI 使用這個欄位，只有 api.ts:2726 與 storage.ts:106 的型別宣告。
- 分鐘資料只有 4 個交易日、只有自選 9 檔，而且是不完整的交易時段（每日 60 到 141 根，看到最早約 09:29 開始）。
- 嚴重度：CONTRIBUTING.md:295 明列「未來函數」屬 P1，所以維持 P1。實際影響很小，因為功能只能從 API 開啟。
    - 證據：
      - backend/app/backtest/strategy.py:948-952, 1265-1269, 1369-1373 reference_price = rolling_mean(market_data.close, 5)[start_id:stop_id]（僅 minute_fill 時）
      - backend/app/backtest/matrix.py:3032-3039 與 _rolling_reduce: out[window-1:] = reducer(sliding_window_view(..., axis=0))，第 t 列含自身
      - backend/app/backtest/matrix.py:2351-2362 傳入的 reference_price 原樣複製、不位移；未傳時取同列 ma5/ma10/ma20
      - backend/app/indicators/pipeline.py:393 ma5 = close.rolling_mean(5).over(symbol)（含當日）
      - backend/app/backtest/engine.py:1814-1827 _refill_price 取 matrix.reference_price[time_id]；買進呼叫點 2105-2107、賣出呼叫點 1903-1905，time_id 為成交當日
      - backend/app/backtest/engine.py:1588-1599 賣出（開盤 ≤ ref 用開盤，低點觸及用 ref）與買進（開盤 ≥ ref 用開盤，高點觸及用 ref）都依 ref 決定成交價
      - backend/app/backtest/engine.py:893-903 獨立模式 _simulate_independent_matrix 同樣取 reference_price[time_id]（原 finding 未提）
      - backend/app/backtest/minute_trigger.py:34 賣出觸發線 (5*ma5 - close)/4 = 前 4 日均價，為當日可知線，證實 finding 的比較基準
      - CONTRIBUTING.md:156 嚴禁未來函數；CONTRIBUTING.md:295 P1 明列「未來函數」
      - 預設與入口：engine.py:74 minute_fill=False；api/backtest.py:345 minute_fill: bool = False；frontend 僅 src/lib/api.ts:2726、src/lib/storage.ts:106 有型別宣告，沒有 UI 使用
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-mfla/verify.py → rolling_mean([1..6],5) = [nan,nan,nan,nan,3.0,4.0]
      - 指令：uv run python AUDIT_SCRATCH/work/verify-mfla/verify2.py（open_t+1，simulate_market_matrix 端到端）：目前寫法收盤 100/105/109.5/120 → ref 100/101/101.9/104 → entry_price 100/101/101.9/104；MA5 shift(1) 與 sum4/4 → 四種情境都是 100.0。finding 的「MA5 104.0 對 100.0、成交 104.0」完全重現
      - 同一 verify2.py，entry_fill=close_t：日K口徑應以 100/105/109.5/120 成交，minute_fill 目前給 100/101/101.9/104，改成因果參考線仍給 100。close_t 模式即使修好參考線，也還是用盤中價成交收盤才確認的訊號
      - verify.py 第 3 部分：simulate_portfolio → build_market_matrix（panel ma5 fallback）→ entry_price 100/101/101.9/104，與矩陣路徑相同
      - verify.py 第 4 部分：本地 taiwan/daily 收盤與官方 twse_stock_day_202608_202609.csv 比對 216 組（代號、日期），0 筆不符；自選 9 檔 × 4 分鐘日（2026-09-17/18/22/23）共 36 個「代號-日」，MA5_t 對 sum4/4 的成交價：買進 3 個不同、賣出 7 個不同，最大相對差 2.9685%（6196.TWSE 2026-09-18 賣出 523.0 對 539.0；3034.TWSE 2026-09-18 買進 549.6 對 547.5）
      - data\kline_minute 只有 date=2026-09-17、09-18、09-22、09-23 四個分區，每日只有自選 9 檔、每檔 60–141 根（非完整 270 根；2454.TWSE 09-22 第一根 01:29 UTC = 09:29 台北）
      - 線上：https://en.wikipedia.org/wiki/Moving_average 原文 "a simple moving average (SMA) is the unweighted mean of the previous k data-points"，公式 SMA_k = (p_{n-k+1}+…+p_n)/k 含當期 p_n；Investopedia 抓取失敗，web search 額度已用完
      - 輸出檔：AUDIT_SCRATCH/work/verify-mfla/verify.out、verify2.out；執行前後 git status --porcelain 相同

### [P3 → P3] market_profile 的賣出稅率一律套用一般股 0.3%，ETF 與債券 ETF 被多收稅（潛在問題）（確認成立）

- **主張**：MatcherConfig.sell_cost_pct 在有 market_profile 時固定取 tax.ordinary_stock_rate，不依標的類別（ETF 0.1%、債券 ETF 0%）。這條路徑目前只有 legacy 與測試會走到。
- **正確的定義或數值**：賣出稅率依證券類別決定（股票 0.3%、ETF 0.1%、債券 ETF 在期限內 0%）。
- **app 實際行為**：所有標的都用 0.3%。
- **對使用者的影響**：目前 market_profile 沒有在正式路徑使用，所以不影響使用者；但如果只接上 profile 而不修這裡，自選的 5 檔 ETF 回測都會被多扣稅。
- **修法建議**：sell_cost_pct 改為依資產別（security master 的 instrument_type、underlying_scope）查表，而不是單一常數。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:97-99 stamp = self.market_profile.tax.ordinary_stock_rate
  - backend/app/taiwan/market_rules.py:111-137 get_tax_rate 支援 TaxClass，但 engine 沒有呼叫
- **線上證據**：
  - https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2條：其他經政府核准之有價證券（ETF 受益憑證）徵千分之一
  - https://quantpass.org/stock-commission/「ETF則為 0.1%」
- **本地計算證據**：
  - verify_engine.py Part 5：market_profile=TaiwanMarketProfile() 時，0050 16100 股這筆，app 損益 12,715.68，依官方 0.1% 稅率應為 16,088，少算 3,372 TWD；00878 少算 3,262 TWD；一般股差異 ≤2 TWD（純四捨五入）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：MatcherConfig.sell_cost_pct（engine.py:95-99）在設定 market_profile 時，賣出稅率固定取 tax.ordinary_stock_rate（0.3%），不依標的類別呼叫 get_tax_rate，所以 ETF 應為 0.1%、債券 ETF 在 2026-12-31 前應為 0%，卻都被當成 0.3%。同時，明確傳入的 stamp_tax_pct 也會被靜默忽略。

這個分支被所有撮合器共用（simulate、正式矩陣路徑、legacy），但正式建構點 strategy.py:1199-1220 沒有傳 market_profile，目前只有 tests/test_taiwan_backtest.py:157 會走到，所以對使用者沒有影響。

以 8/06 開盤買、8/24 開盤賣的情境重現：0050 16,100 股被多扣 3,369.73 TWD（對官方取整口徑差 3,372.32）；00878 50,200 股被多扣 3,260.99 TWD（差 3,262.15）。一般股只有取整造成的 ≤2 TWD 差異。

如果日後只接上 profile 而不修這裡，自選中 4 檔 ETF（0050、00878、00885、00893）的回測都會多扣約 0.2% 的賣出金額。
    - 理由：我用自己寫的腳本重算，finding 的數值都能逐位重現。我把本地 kline_daily_enriched parquet 組成 panel，呼叫純計算函式 BacktestEngine(repo=None).simulate_portfolio（也就是正式的矩陣撮合路徑），設定 open_t+1、資金 1000 萬、6 檔持倉、滑價 0、market_profile=TaiwanMarketProfile()。成交價先和官方 TWSE STOCK_DAY 參考資料逐檔比對，9 檔自選的 8/06 與 8/24 開盤價都完全一致。結果如下：
- 0050：app 損益 12,715.68，官方口徑（0.1% 稅、手續費取整）16,088.00，差 -3,372.32 TWD，與 finding 相同。若不做取整，官方口徑為 16,085.39，差 -3,369.71；這正好等於純稅率差，即賣出金額 1,684,865 × 0.2% = 3,369.73。
- 00878：差 -3,262.15（不取整為 -3,260.96），與 finding 相同。
- 一般股的差異只有 0.40、1.18、1.71、2.08 TWD，都是取整造成，不取整時差異為 0，與 finding「≤2 TWD」相符。
- 另外跑了 9 檔、9 個持倉，4 檔 ETF 都被多扣約 0.2% × 賣出金額：0050 -2,239.49、00878 -2,176.14、00885 -2,179.17、00893 -2,158.58；5 檔一般股差異 ≤0.04。

程式碼方面，engine.py:97-99 在 market_profile 存在時固定取 tax.ordinary_stock_rate；market_rules.py:111-137 的 get_tax_rate 可依 TaxClass 回傳 0.003／0.001／0.0／0.001，但 engine 沒有呼叫它。法規方面，證券交易稅條例第2條規定股票千分之三、「其他經政府核准之有價證券」千分之一；第2條之1 規定債券 ETF 自 106/1/1 至 115/12/31 停徵。所以 ETF 應為 0.1%，債券 ETF 目前為 0%。

finding 有兩處敘述要修正，一處新發現：
1. 「這條路徑目前只有 legacy 與測試會走到」不精確。sell_cost_pct 被 simulate、_simulate_portfolio_matrix（正式路徑）、legacy 三個撮合器共用（engine.py:770、851、1293、1750、2293），只是正式建構點 strategy.py:1199-1220 沒有傳 market_profile。全 repo 唯一有設定它的是 tests/test_taiwan_backtest.py:157。
2. 使用者影響寫「自選的 5 檔 ETF」有誤。官方 ETF 名冊顯示自選中只有 0050、00878、00885、00893 共 4 檔 ETF，而且沒有債券 ETF。
3. 新面向：有 market_profile 時，明確傳入的 stamp_tax_pct 會被靜默忽略。實測帶 stamp_tax_pct=0.001 與不帶的損益逐筆完全相同，所以使用者無法手動覆寫 ETF 稅率。

目前正式路徑沒有使用 market_profile，對使用者沒有影響。P3 合理。
    - 證據：
      - backend/app/backtest/engine.py:95-99 sell_cost_pct 在 market_profile 存在時 stamp = self.market_profile.tax.ordinary_stock_rate，不看標的類別，也不看 stamp_tax_pct
      - backend/app/taiwan/market_rules.py:111-137 get_tax_rate 依 TaxClass 回傳稅率；實測 ordinary_stock=0.003、domestic_etf=0.001、bond_etf=0.0、foreign_etf=0.001，engine 沒有呼叫
      - backend/app/backtest/engine.py:770、851、1293、1750、2293：simulate、independent、legacy、_simulate_portfolio_matrix 都呼叫 config.sell_cost_pct()，不限於 legacy
      - backend/app/backtest/strategy.py:1199-1220 正式 MatcherConfig 沒有傳 market_profile；grep 'market_profile=' 全 repo 只命中 backend/tests/test_taiwan_backtest.py:157
      - 腳本 AUDIT_SCRATCH/work/verify-etftax/repro.py；輸出 repro.out（同目錄），指令 `cd backend && PYTHONIOENCODING=utf-8 uv run python .../repro.py`，exit=0
      - 價格交叉核對：本地 parquet 與官方 twse_stock_day_202608_202609.csv 的 9 檔 8/06、8/24 開盤價全部一致，例如 0050 103.25／104.65、00878 33.12／32.48
      - 本地重算 A（6 檔，market_profile）：0050 shares=16100 app pnl=12715.68，官方取整=16088.00，差=-3372.32；不取整=16085.39，差=-3369.71；純稅率差 1,684,865×0.002=3369.73
      - 本地重算 A：00878 shares=50200 app pnl=-41712.15，官方取整=-38450.00，差=-3262.15；純稅率差 3260.99
      - 本地重算 A：一般股與取整版差 2454 -0.40、3034 -1.18、3545 -1.71、6196 -2.08；與不取整版差 0.00～0.05
      - 本地重算 B：market_profile 加 stamp_tax_pct=0.001，6 檔損益與 A 逐筆完全相同（same=True），明確設定的稅率被忽略
      - 本地重算 C（9 檔自選）：ETF 被多扣 0050 -2239.49、00878 -2176.14、00885 -2179.17、00893 -2158.58；一般股差異 ≤0.04
      - 官方參考 twse_t187ap47_L_etf_meta.csv：0050、00878 為國內成分證券指數股票型基金，00885、00893 為國外成分證券指數股票型基金；自選中 ETF 共 4 檔，沒有債券 ETF
      - https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2條：「公司發行之股票及表明股票權利之證書或憑證徵千分之三」「公司債及其他經政府核准之有價證券徵千分之一」；第2條之1：債券指數股票型基金「自中華民國一百零六年一月一日起至一百十五年十二月三十一日止暫停徵」
      - https://quantpass.org/stock-commission/：「ETF 的證交稅僅需要繳 0.1％（為台股的1/3）」

### [P3 → P3] 步進優化的 train/test/step 以自然日計算，預設 252/63 實際只有約 163～175／36～46 個交易日（確認成立）

- **主張**：generate_folds 用 timedelta(days=…) 計算窗口，但預設值 252/63 是交易日慣例的數字，違反 CONTRIBUTING「窗口按實際交易日，不得用自然日替代」。
- **正確的定義或數值**：窗口依交易日曆切分，或把參數改名、說明為自然日。
- **app 實際行為**：以自然日切分，訓練期約只有 2/3 年。
- **對使用者的影響**：步進優化的樣本量比使用者以為的少約 1/3，過擬合檢驗偏弱。目前沒有前端入口。
- **修法建議**：用 repo 的交易日序列切 fold（依索引前移），或把預設改為 365/91 並在介面標示為自然日。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/walkforward.py:48-53 train_end = train_start + timedelta(days=train_days)
  - backend/app/backtest/walkforward.py:132-134 train_days 252、test_days 63、step_days 63
  - backend/app/api/backtest.py:994-996 預設同上
  - CONTRIBUTING.md:107
- **線上證據**：
  - https://www.ctee.com.tw/news/20251030700042-430201「全年交易日243天」（252 是一年交易日數的慣例值，不是自然日）
- **本地計算證據**：
  - verify_engine.py Part 8：以 enriched 交易日分區計算，252 個自然日窗口內有 163～175 個交易日，63 個自然日窗口內有 36～46 個交易日
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：generate_folds（walkforward.py:48-53）用自然日 timedelta 切窗口，而且是閉區間。預設值 252/63/63（walkforward.py:132-134、api/backtest.py:994-996）實際切出的訓練窗是 253 個自然日，測試窗是 64 個自然日，違反 CONTRIBUTING.md:107 的交易日規範。

以本地台股交易日曆實算（這份日曆和官方 2026-08～09 資料逐日相同）：
- 窗口從交易日起算：訓練窗有 163～175 個交易日，測試窗有 36～46 個。
- 窗口從任意自然日起算：訓練窗 162～175，測試窗 35～46。
- 用預設參數對 2024-01-02～2026-09-23 實際切 11 折：訓練 164～174（平均 168.45，約 252 的 67%），測試 38～46（平均 42.64，約 63 的 68%）。

252/63 是美股慣例，台股一年約 242～243 個交易日。要在台股湊滿 252／63 個交易日，分別需要約 379／93 個自然日（中位數）。

目前沒有前端入口，程式碼內部也一致採用自然日（例如單折上限 186 天，等於 6 個月），所以屬於參數語意和預設值不符規範，維持 P3。

修法：依交易日序列的索引切 fold；或把參數明確標示為自然日。後者若改成 365/91，大約對應一年／一季（約 242～243／60～62 個交易日），不等於 252／63 個交易日。
    - 理由：我自己寫腳本重算，結果和 finding 一致。generate_folds 用 timedelta(days=...) 切窗口，而且是閉區間：train 實際是 [s, s+252]，共 253 個自然日；test 是 [s+253, s+316]，共 64 個自然日。

交易日曆取自本地 kline_daily_enriched 的分區，共 661 個分區，範圍 2024-01-02～2026-09-23，沒有空分區，和 taiwan/daily 的日曆完全相同。這份日曆的 2026-08-03～09-23 區段有 38 個交易日，和官方參考資料 STOCK_DAY、TAIEX 的日期逐日相同，所以可以當成真實的台股交易日曆。

數字比對：
- 窗口從交易日起算：train 窗口有 163～175 個交易日（平均 167.95），test 窗口有 36～46 個（平均 42.78）。finding 寫 163～175／36～46，完全相符。
- 窗口從任意自然日起算：train 162～175，test 35～46。
- 用 API 預設值（start = 最早分區，end = 最後分區）實際切 fold：共 11 折，train 164～174（平均 168.45，是 252 的 0.668 倍），test 38～46（平均 42.64，是 63 的 0.677 倍）。

所以「樣本量比使用者以為的少約 1/3」成立。反過來算，要湊滿 252 個交易日需要 367～391 個自然日（中位數 379）；63 個交易日需要 87～105 個自然日（中位數 93）。

CONTRIBUTING.md:107 規定窗口要按實際交易日計算，程式碼違反這條規則。預設值 252/63 是美股的交易日慣例：Wikipedia 寫 NYSE/NASDAQ 一年平均約 251 個交易日，63 = 252/4。台股一年約 242～243 個交易日：本地日曆 2024 年 242 天、2025 年 243 天；工商時報寫 2026 年全年交易日 243 天。

finding 提到的程式碼位置我都逐一核對過，全部正確：walkforward.py:48-53、walkforward.py:132-134 的預設值、api/backtest.py:994-996 的預設值。前端搜尋 walkforward／train_days 沒有結果，確認目前沒有前端入口。

嚴重度維持 P3，理由有三：
- 沒有前端入口，使用者的正常流程碰不到這段程式碼。
- 程式碼內部一致採用自然日：單折窗口上限 BACKTEST_MAX_SERVER_DAYS=186，錯誤訊息寫「最多支持 6 个月」；測試也用 90/30 這類自然日數字。
- 所以問題在參數命名和預設值的語意與規範不符，不是計算錯誤。

finding 的兩處細節可以更精確：
- 訓練窗口實際是 253 個自然日的閉區間，測試窗口是 64 個。
- 建議改成 365/91：這組自然日窗口大約對應 242～243／60～62 個台股交易日，也就是一年／一季，不會等於 252／63 個交易日。

附帶觀察（不在本 finding 範圍，影響是推測）：相鄰 fold 的 OOS 窗口共用一天。預設切法下 10 個交界日都是交易日。在 open_t+1 撮合下，下一折首日應該沒有部位損益，推測對複利 OOS 沒有實質影響。
    - 證據：
      - backend/app/backtest/walkforward.py:48-53 — train_end = train_start + timedelta(days=train_days)；test_start = train_end + 1 天；test_end = test_start + timedelta(days=test_days)。註解說回測區間是閉區間，所以 train 是 253 個自然日、test 是 64 個自然日
      - backend/app/backtest/walkforward.py:132-134 — train_days 252、test_days 63、step_days 63
      - backend/app/api/backtest.py:994-996 — API 預設值相同；backtest.py:29 BACKTEST_MAX_SERVER_DAYS = 186，錯誤訊息「回测区间最多支持 6 个月」，程式碼內部以自然日為單位
      - CONTRIBUTING.md:107 —「窗口、前 N 日和批次回算均按實際交易日，不得用自然日直接替代。」
      - 前端 frontend/src 用 Grep 搜 walkforward|walk-forward|train_days：沒有結果（沒有前端入口）
      - 腳本：AUDIT_SCRATCH/work/verify-wfdays/verify_wf_days.py；輸出：同目錄 out.txt。指令：cd backend && uv run python verify_wf_days.py，exit=0
      - 本地日曆：kline_daily_enriched 661 個分區（2024-01-02～2026-09-23），沒有空分區，和 taiwan/daily 日曆相同；每年交易日數：2024 年 242 天、2025 年 243 天、2026 年截至 9/23 共 176 天；沒有週末日期
      - 官方比對：reference/csv/twse_stock_day_202608_202609.csv 有 38 個日期（2026-08-03～2026-09-23），和本地同區段 38 個分區逐日相同；TAIEX（index_daily_202608_202609.csv）也相同
      - 重算 train [s,s+252]：從交易日起算 min 163／max 175／mean 167.95；從任意自然日起算 162～175。finding 是 163～175 → 相符
      - 重算 test [s,s+63]：從交易日起算 min 36／max 46／mean 42.78；從任意自然日起算 35～46。finding 是 36～46 → 相符
      - 用預設參數實際切 fold（2024-01-02～2026-09-23）：11 折，train 交易日 164～174（mean 168.45，是 252 的 0.668 倍）；test 38～46（mean 42.64，是 63 的 0.677 倍）
      - 湊滿 N 個交易日需要的自然日（閉區間）：252 個交易日需要 367～391 天（中位數 379）；63 個交易日需要 87～105 天（中位數 93）
      - https://www.ctee.com.tw/news/20251030700042-430201（2025-10-30，指 2026 年）：「全年交易日243天，另還有9個3天以上連假休市」
      - https://en.wikipedia.org/wiki/Trading_day：「The NYSE and NASDAQ average about 251 trading days a year.」（252/63 是美股的交易日慣例）
      - git status 前後相同（只有既有的 3 個未提交修改），本次沒有修改 repo

### [P3 → P3] close_t 進場時，移動停損與移動停利的峰值包含進場前的當日高點（確認成立）

- **主張**：組合模式在建倉後，於同一日迴圈尾端用當日 high 更新 max_high；獨立模式在建倉時直接取 max(entry_price, high)。close_t 進場時，當日高點發生在收盤成交之前，不屬於持倉期間，卻被當成峰值，使移動停損線過高而提早出場。
- **正確的定義或數值**：close_t 進場時，峰值從進場價起算，隔日才納入 high；open_t+1 進場則可以納入當日 high。
- **app 實際行為**：兩種模式都把進場日的高點計入峰值。
- **對使用者的影響**：只影響 close_t 進場並設定移動停損或移動停利的回測；策略回測 API 的預設是 open_t+1，所以影響有限。
- **修法建議**：依 entry_fill 決定是否把進場 bar 的 high 納入峰值。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:2134、2143-2146 新建倉也用 high[time_id] 更新 max_high
  - backend/app/backtest/engine.py:1103 "max_high": max(entry_price, float(matrix.high[time_id, asset_id]))
- **線上證據**：
  - 無（屬撮合邏輯；依據為持倉峰值只能取進場後的價格）
- **本地計算證據**：
  - verify_matrix_load.py 合成測試：9/01 高 110、收 100（close_t 進場 100），9/02 低 104、高 107，移動停損 5%。組合模式與獨立模式都在 9/02 以 104.5 trailing_stop 出場；若只用進場後的峰值 107，停損線為 101.65，不應觸發
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：finding 的主張成立。組合模式在建倉的同一日迴圈尾端（engine.py:2143-2146），用當日 high 更新新倉的 max_high；獨立模式在建倉時直接取 max(entry_price, high)（engine.py:1103）。close_t 以收盤價成交，進場日的最高價發生在成交之前，卻被當成持倉峰值，墊高移動停損線與移動停利線。

合成情境：close_t 進場 100、進場日最高 110、移動停損 5% 時，兩種模式都在隔日以 104.5 出場（trailing_stop）；移動停利（啟動 5%、回撤 3%）則在隔日以開盤價 105 出場（trailing_take_profit）。峰值從進場價起算時，兩者都不會出場。

需要更正一處：finding 說「只用進場後峰值 107 時停損線為 101.65」並不精確。引擎在 9/02 檢查時用的是之前的峰值，正確的停損線是 95.00。結論不變。

真實資料：自選 9 檔的官方 OHLC，2026-08 至 2026-09 共 333 筆 close_t 進場：
- 移動停損 5%：55 筆結果不同，其中 36 筆提早出場、19 筆同日但出場價較高，沒有一筆晚出場。平均損益 1.676%，修正版為 1.755%。單筆最大差距 17.8 個百分點（2454 在 08-20 進場：-0.64%，修正版 +17.21%）。
- 移動停損 3%：106 筆結果不同。
- 單筆損益有好有壞，不是一律變差。

open_t+1 納入成交日的最高價是正確的。策略 API 預設 open_t+1；前端沒有呼叫策略回測；挖掘流程固定用 open_t+1。因此只有直接呼叫 API 並指定 entry_fill=close_t 才會觸發，嚴重度維持 P3。
    - 理由：這條 finding 成立，嚴重度維持 P3。

一、程式碼部分：兩處引用都成立。
- 組合模式：新建倉時先把 max_high 設成 entry_price（engine.py:2134），之後在同一個 time_id 的迴圈尾端用當日 high 更新所有持倉（2143-2146），當天剛建的倉也包括在內。
- 獨立模式：建倉時直接把 max_high 設成 max(entry_price, high[time_id])（1103）。
- 用到這個峰值的地方：兩種模式都在隔天的風控檢查拿 max_high 算移動停損線與移動停利線（956-966、2006-2016），進場當天則跳過（950、1998）。
- close_t 以當日收盤價成交（848、1747）。進場日的最高價一定發生在收盤成交之前，不屬於持倉期間。open_t+1 以開盤價成交，當日最高價發生在開盤之後，納入峰值是正確的。
- 生產路徑確實走這兩個矩陣版本：strategy.py:1520 與 1529。legacy 版本行為相同（1509-1511、2727-2732），但沒有生產呼叫者。

二、合成情境重現：數字與 finding 一致。
- close_t，9/01 最高 110、收盤 100，移動停損 5%：組合與獨立兩種模式都在 9/02 以 104.5 觸發 trailing_stop，與 finding 相同。
- 對照組把 9/01 最高價改成 100：兩種模式都不出場，到 9/04 以 106 的 end 結束。這證明觸發原因就是進場前的當日高點。
- open_t+1 同樣在 104.5 出場。這種情況下峰值 110 發生在開盤成交之後，屬於合理結果。
- 移動停利（啟動 5%、回撤 3%）也重現：引擎在 9/02 以開盤價 105 觸發 trailing_take_profit；峰值從進場價起算時不會觸發，到 9/04 以 106 結束。
- finding 有一個小誤差，不影響結論。它說「只用進場後峰值 107，停損線為 101.65」，但引擎在 9/02 檢查時用的是 9/02 之前的峰值。依正確規則，9/02 的停損線應為 100×0.95=95.00，不是 101.65。兩者都不會被最低價 104 觸發。

三、真實資料重現：
- 資料：本地 parquet 中自選 9 檔在 2026-08-03 至 2026-09-23 的 OHLC，共 342 列，與 TWSE 官方 STOCK_DAY 逐列比對，不一致 0 列。
- 做法：每一天都當作一次 close_t 進場（最後一日不進場，共 333 筆），各跑一次原始引擎與「只把進場日 high 改成收盤價」的版本。
- 結果：引擎的出場從未晚於修正版，這支持「停損線過高、提早出場」的描述。
- 但單筆損益不是一律變差：同日出場時引擎的停損價較高，有時結果反而較好。平均損益是修正版略高。

四、手算核對：2454 在 2026-08-20 進場（收盤 3700、最高 3870）。
- 引擎的停損線是 3870×0.95=3676.5；8/25 開盤 3725、最低 3655，於是以 3676.5 出場，損益 -0.64%。
- 峰值從 3700 起算時，要到 9/03 才出場：峰值 4565×0.95=4336.75，當日最低 4290，損益 +17.21%。
- 兩者都與腳本輸出一致。

五、線上佐證：backtrader 的 StopTrail 文件說明，初始停損由「最近一根收盤價」減去 trailamount 求得，之後價格上漲才往上調。這表示收盤後掛出的移動停損應從收盤價起算，而不是該根 K 棒的最高價。

六、嚴重度：維持 P3。
- 就程式邏輯而言，這是明確的撮合錯誤。
- 但觸發條件受限：策略回測 API 與 StrategyBacktestConfig 的預設都是 open_t+1（api/backtest.py:331、strategy.py:527）；挖掘流程固定用 open_t+1（mining_runtime.py:292-293）。
- 前端的 strategyBacktestRun 只有定義（api.ts:2708），沒有任何呼叫者，也沒有呼叫 strategy/stream 或 strategy/cancel。
- 預設 close_t 的訊號回測 /api/backtest/run 不支援移動停損，請求參數只有 stop_loss_pct（api/backtest.py:79-90）。
- 因此使用者目前只有直接呼叫 API 並指定 entry_fill=close_t 才會碰到。依使用者情境，實際影響很小。
    - 證據：
      - backend/app/backtest/engine.py:2134：新倉 "max_high": entry_price；2143-2146：同一 time_id 迴圈尾端 for asset_id, pos in positions.items(): pos["max_high"] = max(pos["max_high"], high[time_id])，涵蓋當日新建倉
      - backend/app/backtest/engine.py:1103："max_high": max(entry_price, float(matrix.high[time_id, asset_id]))，close_t 與 open_t+1 都這樣做
      - backend/app/backtest/engine.py:848、1747：close_t 以 matrix.close 成交；956-966、2006-2016：用 max_high 算 trailing_stop 與 trailing_take_profit 線；950、1998：進場當日跳過風控
      - backend/app/backtest/strategy.py:1520、1529：生產路徑呼叫 simulate_independent_market_matrix 與 simulate_market_matrix
      - 腳本 AUDIT_SCRATCH/work/verify-trailpeak/repro.py，輸出 out.txt：close_t、移動停損 5%、9/01 最高 110：portfolio 與 independent 都是『out 2026-09-02@104.5 trailing_stop』，與 finding 的 104.5 相同；9/01 最高改 100 的對照組兩種模式都是『out 2026-09-04@106.0 end』
      - 同一輸出：移動停利（啟動 5%、回撤 3%）在 9/01 最高 110 時，兩種模式都是『out 2026-09-02@105.0 trailing_take_profit』；9/01 最高 100 時都是『out 2026-09-04@106.0 end』
      - 數字對照：finding 說只用進場後峰值 107 時停損線為 101.65；實際上引擎 9/02 檢查用的是 9/02 之前的峰值，修正後的停損線應為 100×0.95=95.00。兩者都不會被最低價 104 觸發，結論相同
      - 官方比對：本地 data/taiwan/daily 自選 9 檔 2026-08-03 至 2026-09-23 共 342 列，與 reference/csv/twse_stock_day_202608_202609.csv 逐列比對 OHLC，不一致 0 列（out.txt：local rows=342 ref-joined rows=342 OHLC mismatches=0）
      - summary.py 輸出 summary_out.txt，333 筆 close_t 候選：[trailing_stop 5%] differing=55 engine_exit_earlier=36 later=0 same_day_diff_price=19，mean_pnl engine=1.6760% vs peak_from_entry=1.7553%，max_abs_pnl_gap=17.8446%
      - summary_out.txt：[trailing_stop 3%] differing=106 earlier=68 later=0 same_day=38，mean 0.9028% vs 1.0022%；[trailing_tp 啟動 5% 回撤 3%] differing=8 earlier=7 later=0 same_day=1，mean 3.6829% vs 3.7787%
      - 手算（官方 CSV）：2454 在 2026-08-20 以收盤 3700 進場、當日最高 3870。引擎停損線 3676.5，8/25 開盤 3725、最低 3655，以 3676.5 出場，損益 -0.635%；峰值從 3700 起算時，9/03 停損線 4565×0.95=4336.75、最低 4290，以 4336.75 出場，損益 +17.21%；與腳本的 -0.006351 與 0.172095 一致
      - 反例（單筆方向不一定變差）：2454 在 2026-08-05 進場。引擎 8/06 以 3918.75 出場，損益 -2.03%；修正版 8/07 以 3895 出場，損益 -2.63%
      - https://www.backtrader.com/docu/order-creation-execution/trail/stoptrail/ 原文："If no price is specified, the latest close price is used"、"trailamount is substracted from the price to find the stop"、"If the new price goes up, it is updated"
      - 觸發條件：backend/app/api/backtest.py:331 與 strategy.py:527 預設 open_t+1；mining_runtime.py:292-293 固定 open_t+1；frontend\src\lib\api.ts:2708 的 strategyBacktestRun 在 frontend/src 內沒有任何呼叫者；api/backtest.py:79-90 的訊號回測只有 stop_loss_pct，沒有移動停損參數

### [P3 → P3] 策略回測的基準曲線寫死上證指數（000001.SH）（確認成立）

- **主張**：_build_benchmark_curve 固定讀 000001.SH，名稱也寫死為「上证指数」；純台股部署下 kline_index_daily 為空，基準曲線恆為空，也不會用加權指數。
- **正確的定義或數值**：台股回測以 TAIEX（或 0050 含息）作為基準。
- **app 實際行為**：基準為空；有資料時會是 A 股指數。
- **對使用者的影響**：回測結果缺少對照基準，無法判斷超額報酬。目前沒有前端入口。
- **修法建議**：依標的市場選擇基準：台股讀 data/taiwan/index_daily 的加權指數。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/strategy.py:59 BENCHMARK_SYMBOL = "000001.SH"
  - backend/app/backtest/strategy.py:1927-1951 name: 上证指数
- **線上證據**：
  - 無（屬市場歸屬問題）
- **本地計算證據**：
  - ls data/kline_index_daily | wc -l 輸出 0；台股指數在 data/taiwan/index_daily
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：_build_benchmark_curve（strategy.py:1927-1951）固定用 000001.SH（上證綜指）查 repo.get_index_daily，名稱寫死為「上证指数」。這條路徑實際讀的是 data/kline_index_enriched，不是 finding 寫的 kline_index_daily；純台股部署下兩個目錄都是 0 個檔，所以基準曲線恆為空，也不會改用 data/taiwan/index_daily 裡的 t00.TWSE（加權指數）。實測：兩個區間都回傳 0 列；本地 t00.TWSE 與官方收盤 34 日完全一致，8/3～9/17 報酬 +6.69%。finding 沒提到的後果：full 模式在基準為空時把 benchmark_return 設為 0.0（strategy.py:1695-1699），stats 的 excess 因此靜默等於 total_return（實測 0.3612 = 0.3612），而不是標示成缺值。目前前端沒有呼叫 /api/backtest/strategy/run 的入口，mining 也關閉基準，所以使用者現行流程不受影響，維持 P3。修法：依標的市場選基準，台股改讀 data/taiwan/index_daily 的 t00.TWSE；基準缺值時 excess 應回傳 null，不應以 0 代入。
    - 理由：我親自重現了這條 finding 的核心主張，並補上兩處修正。

1) 程式碼：strategy.py:59 把 BENCHMARK_SYMBOL 寫死為 "000001.SH"。_build_benchmark_curve（strategy.py:1927-1951）只用這個代號讀 repo.get_index_daily，並把 name 寫死為「上证指数」（1946-1947 行）。整個函式沒有依標的市場切換基準的分支。

2) 數據重現：我寫了唯讀腳本，用 types.MethodType 把真正的 KlineRepository.get_index_daily、_scan_index_daily_symbol 綁到 stub 上，再呼叫真正的 StrategyBacktestService._build_benchmark_curve。兩個區間 2024-08-01..2026-09-23 與 2026-08-03..2026-09-23 的回傳長度都是 0。同一路徑改查 t00.TWSE 也回傳 0 列，所以這條讀取路徑不管用哪個代號都拿不到加權指數。

3) 證據路徑要修正：finding 引用的 data/kline_index_daily 確實有 0 個檔，但 get_index_daily 實際讀的是 data/kline_index_enriched（repository.py:361 的 _index_enriched_glob，1797 行 scan）。這個目錄同樣是 0 個檔，所以結論不變，只是證據目錄寫錯。

4) 台股指數資料確實在別處，而且正確：data/taiwan/index_daily 有 519 個分區檔，symbol 為 t00.TWSE／o00.TPEX，日期 2024-08-01→2026-09-17。它與官方參考資料（TWSE MI_5MINS_HIST）8/3～9/17 共 34 個交易日逐日比對收盤，最大差 0.0。同期 TAIEX 報酬：官方 +6.6878%，本地 +6.6878%。所以建議的修法（改讀這份資料）可行。

5) 回測面板確實是台股：kline_daily_enriched 最新分區 2026-09-23 共 1323 列，全部是 .TWSE。可見純台股部署下，透過 API 跑的回測是台股回測，卻只能對到空的 A 股基準。

6) finding 沒提到的新面向：full 模式（_run_full_simulation）在基準為空時，benchmark_return 預設為 0.0（strategy.py:1695-1699），而 stats["excess"] = total_return − 0（1742-1743 行）。結果是超額報酬靜默等於總報酬，而不是標示成缺值。實測用自選 9 檔、8/3～9/23 的 panel、全部進場、持有 5 日：total_return=0.3612、benchmark_return=0.0、excess=0.3612，兩者相等為 True。同期 TAIEX 實際漲 +6.69%，所以 excess 被高估，而且看起來像有效數字。

7) 前端入口：frontend/src/lib/api.ts:2708 雖然定義了 strategyBacktestRun，但全 frontend/src 沒有任何呼叫端。navigation.ts:25-30 的註解也寫明 /backtest 已移除、/api/backtest 沒有前端消費端。mining_runtime.py:78 用 include_benchmark=False。所以使用者目前的流程碰不到這個問題，維持 P3 合理。excess 口徑錯誤本身屬於金融口徑問題，但目前只能從 API 觸發，所以不升級。

8) 線上核實：Wikipedia 的 SSE Composite Index 頁面說明代號 000001 為上海證券交易所綜合指數，涵蓋「all stocks (A shares and B shares) that are traded at the Shanghai Stock Exchange」。這證實了「有資料時會是 A 股指數」的說法。

附帶觀察，不屬於本 finding：本地 taiwan/index_daily 缺 9/18、9/21、9/22、9/23 四天，比官方參考資料落後。另外 app/services/regime_builder.py:446 也把 000001.SH 當預設代號，未查證其影響。

腳本執行前後 git status 相同，data/ 與 backend/app 下沒有新檔。
    - 證據：
      - backend/app/backtest/strategy.py:59 BENCHMARK_SYMBOL = "000001.SH"
      - backend/app/backtest/strategy.py:1929 df = self.engine.repo.get_index_daily(BENCHMARK_SYMBOL, start, end, columns=["date", "close"])；1946-1947 "name": "上证指数", "symbol": BENCHMARK_SYMBOL
      - backend/app/repository.py:361 _index_enriched_glob = data_dir/kline_index_enriched/**/*.parquet；1795-1807 _scan_index_daily_symbol 讀的是這個 glob，不是 kline_index_daily
      - backend/app/backtest/strategy.py:1695-1699 benchmark_return = 0.0，只有 benchmark_curve 非空時才覆寫；1742-1743 "benchmark_return"、"excess": total_return - benchmark_return
      - 本地指令：cd backend && uv run python scratchpad/audit/work/verify-bench/verify_benchmark.py（輸出存於 AUDIT_SCRATCH/work/verify-bench/output.txt）→ kline_index_daily: 0 parquet files；kline_index_enriched: 0 parquet files；taiwan/index_daily: 519 parquet files
      - 同一輸出：呼叫真實 _build_benchmark_curve：2024-08-01..2026-09-23 len = 0；2026-08-03..2026-09-23 len = 0；同一 repo 路徑查 t00.TWSE：0 rows
      - 同一輸出：taiwan/index_daily symbols ['o00.TPEX','t00.TWSE']，t00.TWSE 共 519 列，2024-08-01→2026-09-17；對照官方 reference/csv/index_daily_202608_202609.csv，34 個重疊交易日 max |local-ref| close = 0.0；TAIEX 2026-08-03..2026-09-17 報酬：ref 6.6878%、local 6.6878%；本地缺 2026-09-18、09-21、09-22、09-23
      - 同一輸出：kline_daily_enriched 最新分區 date=2026-09-23 共 1323 列，後綴全為 TWSE（1323）
      - 同一輸出：full 模式 _run_full_simulation（自選 9 檔，panel 342 列，持有 5 日）→ {'total_return': 0.3612, 'benchmark_return': 0.0, 'excess': 0.3612}；excess == total_return: True
      - frontend/src/lib/api.ts:2708 定義 strategyBacktestRun，但 frontend/src 沒有任何呼叫端（grep 只命中定義處）；frontend/src/lib/navigation.ts:25-30 註明 /backtest 已移除、/api/backtest 沒有前端消費端
      - backend/app/backtest/mining_runtime.py:78 include_benchmark=False（mining 不取基準）
      - 線上：https://en.wikipedia.org/wiki/SSE_Composite_Index：代號 000001，涵蓋 "all stocks (A shares and B shares) that are traded at the Shanghai Stock Exchange"，中文名「上海证券交易所综合股价指数／上证综指」
      - 無寫入證據：腳本執行前後 git status --porcelain 完全相同，data/ 與 backend/app 下沒有比基準檔更新的檔案（numba cache 導向 scratchpad）

### [P2 → P3] 正式撮合寫死每單位 100 股（A 股一手），不符台股整股 1000 股／零股 1 股（部分成立）

- **主張**：組合模式以 floor(資金/價格/100)×100 決定股數，lots＝shares/100；獨立候選模式固定 shares=100、lots=1.0。TaiwanMarketProfile.lot 只在 legacy 路徑使用，正式路徑完全不讀。成交價（停損觸價等）也沒有對齊台股升降單位。
- **正確的定義或數值**：依台股整股（1000 股）或明確的零股模式（1 股）決定股數，lots 以 1000 股為一張；成交價依升降單位取整。
- **app 實際行為**：以 100 股為單位下單，張數欄位以 100 股為一張。
- **對使用者的影響**：回測的部位大小、現金閒置比例與能否買進都和台股實務不同：高價股如 2454，在整股規則下買不起、零股規則下可買 40 股，app 卻給 0 股；中價股則買進台股不存在的 300 股「整股」。交易明細的張數欄位高估 10 倍。每筆報酬率本身不受影響。
- **修法建議**：正式撮合從 MatcherConfig 取 lot_size（台股預設 1000，可選零股 1），lots 改用同一個 lot_size；觸價成交用 TickSizeModel 取整。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:2108 shares = np.floor(allocation / (entry_price * (1 + buy_cost_pct)) / 100) * 100
  - backend/app/backtest/engine.py:2131 "lots": shares / 100
  - backend/app/backtest/engine.py:1026,1041 shares = 100.0、lots=1.0
  - backend/app/backtest/engine.py:2661-2663 只有 legacy 的 simulate_portfolio_legacy 讀 market_profile.lot
  - docs/taiwan-market-contract-v1.md:7「ordinary board-lot size is 1,000 shares/units and odd-lot minimum is one」
- **線上證據**：
  - https://www.managertoday.com.tw/articles/view/59023、https://rich01.com/what-is-settlement-period-t-plus-2-mean/（搜尋結果摘要）：「一張股票為1000股，零股交易單位為1股-999股」
- **本地計算證據**：
  - 指令：verify_engine.py Part 6（本金 100 萬、6 格，8/06 開盤價）：3034@547 app 買 300 股（整股規則為 0 股、零股規則為 304 股）；6196@501 app 300 股（整股 0、零股 332）；0050@103.25 app 1600 股、lots=16（整股 1000 股＝1 張）；2454@4100 app 0 股（零股規則可買 40 股）
  - 獨立候選模式六筆交易都是 shares=100.0、lots=1.0
  - verify_exdiv.out：2379 停損成交價 691.61，不在 500～1000 元區間的 1 元跳動單位上
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：正式撮合的兩條路徑（_simulate_portfolio_matrix、_simulate_independent_matrix）都以 A 股的 100 股為一單位：組合模式把股數取到 100 股整數倍，lots＝shares/100；獨立候選模式固定 shares=100、lots=1.0。停損觸價以計算出的停損價成交，沒有對齊台股升降單位（例如 693.78 元），偏差不到 1 個升降單位，而且偏樂觀。這和台股規則不符：一張 1000 股、零股最小 1 股、500～1000 元升降單位 1 元。整個後端沒有任何地方設定 MatcherConfig.market_profile，所以 TaiwanMarketProfile 的 LotModel 在所有路徑（包括 legacy）都不會生效。影響方面：組合模式的部位大小與現金使用率和台股實務不同，例如 2454@4100 在本金 100 萬、6 格的設定下被判為買不起；API 回傳的 lots 以 100 股為一張。獨立候選模式的統計只用 pnl_pct，不受影響。在使用者的純台股部署中，前端已沒有回測頁面，lots、shares 也沒有任何 UI 顯示，只有直接呼叫 /api/backtest/strategy/run 才會碰到，所以嚴重度應為 P3，不是 P2；之後若要重新對台股開放回測，這條應升回 P2 並先修正。
    - 理由：程式碼的主張成立，我也親自跑過確認。正式撮合路徑把 100 股寫死成一單位：組合模式用 floor(配置金額/價格/100)×100，lots＝shares/100；獨立候選模式固定 shares=100、lots=1.0。停損以觸價直接成交，成交價沒有對齊升降單位。台股的正確規則也已確認：一張 1000 股，零股最小 1 股；升降單位在 500～1000 元區間是 1 元。

finding 有一點說得不精確。它說「TaiwanMarketProfile.lot 只在 legacy 路徑使用」，實際上整個後端沒有任何地方設定 MatcherConfig.market_profile（strategy.py:1199-1220 建立設定時就沒傳這個參數），所以連 legacy 路徑也一樣用 100 股。

嚴重度需要下修。在使用者情境（純台股部署）下，前端沒有任何回測入口：/backtest 頁面與路由已經刪除，navigation.ts 明寫「/api/backtest 仍註冊但無前端消費端」，理由是「功能尚未支援台股」；miningTask.ts 也沒有被任何頁面引用。整個 frontend/src 只有 api.ts 的型別定義提到 lots、shares 和 pnl_amount，沒有元件顯示它們。因此 finding 說的「交易明細的張數欄位高估 10 倍」使用者在 UI 上看不到，只有直接呼叫 /api/backtest/strategy/run 的人才會碰到。

獨立候選模式的統計只用 pnl_pct，所以固定 100 股不影響它的任何統計數字，只影響明細的 shares、lots 和金額欄位。停損價沒對齊升降單位，偏差不到 1 個升降單位，而且是偏樂觀的方向（例如 693.78 元，實際成交應在 693 元以下），約 0.14%。

綜合來看，這是一個尚未對台股開放的功能的口徑不一致，不是目前使用者流程裡的明確功能錯誤，所以嚴重度從 P2 降為 P3。之後若要把回測重新開放給台股使用，這條應該升回 P2，並且修正。
    - 證據：
      - backend/app/backtest/engine.py:2108 `shares = np.floor(allocation / (entry_price * (1 + buy_cost_pct)) / 100) * 100`；:2131 `"lots": shares / 100`（正式組合路徑 _simulate_portfolio_matrix）
      - backend/app/backtest/engine.py:1026 `shares = 100.0`、:1041 `lots=1.0`（正式獨立候選路徑 _simulate_independent_matrix）；:1428/:1444 legacy 版也相同
      - backend/app/backtest/engine.py:2661-2663 只有 simulate_portfolio_legacy 會讀 config.market_profile.lot；engine.py:75 註解寫「未设置时回退 A 股默认规则」
      - Grep 整個 backend\app，market_profile 只出現在 engine.py；backend/app/backtest/strategy.py:1199-1220 建立 MatcherConfig 時沒有傳 market_profile，所以 TaiwanMarketProfile 在任何路徑都不會生效
      - 正式路徑：strategy.py:1519-1535，mode=='full' 走 simulate_independent_market_matrix，其他模式走 simulate_market_matrix
      - backend/app/backtest/engine.py:973 停損觸價時 `return reason, stop_price`，沒有依升降單位取整
      - backend/app/backtest/engine.py:3011 獨立候選模式的統計只用 t.pnl_pct，固定 100 股不影響統計
      - frontend/src/lib/navigation.ts:25-31「/backtest 移出 CORE_NAV…使用者實際無法選取台股標的…route 與 component 其後已刪除…backend 的 /api/backtest 仍註冊但無前端消費端」
      - Grep frontend\src：lots、shares、pnl_amount、entry_value 只出現在 frontend/src/lib/api.ts:1850-1855 的型別定義，沒有元件顯示；miningTask.ts 沒有被任何頁面 import
      - backend/app/main.py:424 app.include_router(backtest.router)：API 仍然可以直接呼叫；strategy.py:2065-2066 回應 JSON 含 shares、lots
      - 本地實跑：`cd backend && uv run python scratchpad\audit\work\verify_lot_def\lot_check.py`（本金 100 萬、6 格、8/06 開盤價），組合模式輸出 0050 1600.0 股／lots 16.0、00878 5000.0／50.0、3034 300.0／3.0、3545 3300.0／33.0、6196 300.0／3.0，2454@4100 被略過（execution 的 buy_lot_size=1）；獨立候選模式六檔全部是 shares 100.0、lots 1.0；2379 進場 746、停損 7%，出場價 693.78（stop_loss）
      - 本地實跑 tick_check.py：用官方 TWSE STOCK_DAY 參考資料（reference\csv\twse_stock_day_202608_202609.csv）的 1520 個 OHLC 價格對照升降單位，0 筆不在格點上（1.0 元級距 409 筆），證實升降單位規則成立；693.78 不在 1 元格點上
      - 線上來源（不同於 finding 引用的網站）：https://zh.wikipedia.org/wiki/臺灣證券交易所 原文「一般股票以1,000股為一交易單位，未滿1,000股可透過零股交易」「新臺幣ETF為例，每張交易單位為1,000受益權單位」
      - 升降單位的線上來源沒有取得：WebSearch 本 session 額度已用完（200/200），嘗試的 Wikipedia 頁面不是沒有升降單位表就是回 404；升降單位改以 repo 的 docs/taiwan-market-contract-v1.md:27 與上述官方價格格點實證佐證
      - 稽核前後 git status --porcelain 都只有原本的 3 個 M 檔，沒有新增異動
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：正式回測撮合確實寫死每單位 100 股（A 股一手）：組合模式以 floor(資金/價格/100)×100 決定股數，lots=shares/100（engine.py:2108、2131）；獨立候選模式固定 shares=100、lots=1.0（engine.py:1026、1041）。TaiwanMarketProfile.lot 只在 legacy 路徑讀取，而且正式路徑的 MatcherConfig（strategy.py:1199-1219）根本沒有傳 market_profile。停損、移動停損、止盈的觸價成交價沒有對齊台股升降單位，與往下取到跳動單位的價格相比差距約 0.1%。以本金 100 萬、6 格、8/06 開盤價實測，股數與 finding 所列完全一致：3034 300 股（整股 0／零股 304）、6196 300 股（0／332）、0050 1600 股、lots=16（1000／1613）、2454 0 股（0／40）。每筆交易的報酬率不受影響。修正點：finding 描述的使用者影響，在這個部署中不成立。前端沒有回測頁面，也沒有元件呼叫回測 API，lots 欄位從未顯示；docs/features.md:42 也聲明產品不提供策略回測平台。這個錯誤只有直接呼叫 /api/backtest/strategy/run 時才會出現，屬於對使用者不可見的潛在缺陷，嚴重度應為 P3，不是 P2。
    - 理由：結論：finding 在程式碼層面的主張與所有股數數字都完整重現，但它對使用者影響的描述不符合這個部署的實際情況。因此判為 partially_confirmed，嚴重度從 P2 降為 P3。

【重現方式】我寫了 repro.py，用本地 data/taiwan/daily 的 parquet 自組 panel（8/05 發出進場訊號，entry_fill=open_t+1，所以在 8/06 開盤價成交），再用正式路徑的 simulate_market_matrix 與 simulate_independent_market_matrix 實跑。本金 100 萬、分 6 格，每格約 166,667 元。本地 8/06 開盤價與官方參考資料 twse_stock_day 完全一致：2454=4100、3034=547、6196=501、0050=103.25、2379=746、3545=50。

【數字對照，app／1000 股整股規則／1 股零股規則】
- 2454：0／0／40，finding 寫 0 股、零股 40，相符。
- 3034：300／0／304，相符。
- 6196：300／0／332，相符。
- 0050：1600 股、lots=16／1000／1613，相符。
- 2379：200／0／223，finding 未列。
- 3545：3300／3000／3331，finding 未列。
- 把手續費改成台股常見的 0.001425 之後，app 算出的股數不變，零股規則則變成 40／304／332／1611／222／3326。
- 獨立候選模式的 6 筆交易都是 shares=100.0、lots=1.0，相符。

【程式碼確認】
- 組合模式：engine.py:2108 用 /100*100 決定股數，engine.py:2131 寫 lots=shares/100。
- 獨立候選模式：engine.py:1026 固定 shares=100.0，engine.py:1041 固定 lots=1.0。
- 只有 legacy 路徑的 engine.py:2661-2663 會讀 market_profile.lot，而且預設值也是 100。
- 此外，正式路徑建立 MatcherConfig 的 strategy.py:1199-1219 根本沒有傳 market_profile，所以 TaiwanMarketProfile 在正式回測裡完全沒有作用。

【升降單位】
- 我先驗證了升降單位本身：官方 1520 個 OHLC 價格全部落在 TickSizeModel 的跳動單位上，不在單位上的是 0 個。
- app 算出的停損成交價則不在跳動單位上，例如停損 7% 時：2379 為 693.78（跳動單位 1）、3034 為 508.71、6196 為 465.93（跳動單位 0.5）、2454 為 3813.0（跳動單位 5）。
- 和往下取到跳動單位的價格相比，差距只有 0.08%～0.14%。
- finding 提到的 691.61 來自另一個除息情境，我沒有重現那個確切數字，但重現了同一個現象。

【線上查證】managertoday 原文寫「1 張股票等於 1000 股」「從 1 股到 999 股，都可以透過零股交易買賣」；中文維基「臺灣證券交易所」條目也寫「一般股票以1,000股為一交易單位」。升降單位表沒能在網路上找到：WebSearch 額度已用完，猜測的網址都回 404。所以升降單位改用官方成交價做實證，也就是上面 1520 筆全部落在跳動單位上的結果。

【影響被高估的部分】
- 前端路由 router.tsx:82-121 沒有任何回測頁面。
- 除了 lib/api.ts 本身，沒有任何元件呼叫 strategyBacktestRun、backtestRun 或 startMining。
- lots 欄位在前端完全沒有被渲染。
- docs/features.md:42 明文寫「本產品不提供……舊式策略回測平台」。
- data/backtest_results 是空的；job_store 的 5 筆挖掘工作全部在初始化時失敗，錯誤訊息是「A 股日K修正管道已下线」。

所以 finding 說「交易明細張數欄位高估 10 倍」、會影響使用者的回測部位，在這個純台股、只用 UI 的部署中並不會發生。這些錯誤只會出現在直接呼叫 /api/backtest/strategy/run 的時候（main.py:424 仍然掛著這個 router）。

【嚴重度】屬於潛在的功能錯誤，但對使用者不可見，依 CONTRIBUTING 第 11 節判為 P3。每筆交易的報酬率不受股數影響，這一點與 finding 一致。
    - 證據：
      - backend/app/backtest/engine.py:2108 shares = np.floor(allocation / (entry_price * (1 + buy_cost_pct)) / 100) * 100；:2131 "lots": shares / 100
      - backend/app/backtest/engine.py:1026 shares = 100.0；:1041 lots=1.0（_simulate_independent_matrix，正式的獨立候選路徑）
      - backend/app/backtest/engine.py:2661-2664 只有 simulate_portfolio_legacy 讀 market_profile.lot，預設 lot_size = 100
      - backend/app/backtest/strategy.py:1199-1219 正式路徑的 MatcherConfig(...) 沒有傳 market_profile；:1520 與 :1529 是兩條正式撮合入口
      - backend/app/taiwan/market_rules.py:158-178 LotModel 預設 board_lot_size=1000、odd_lot_size=1，但正式撮合沒有使用
      - 本地重現：cd backend && uv run python scratchpad\audit\work\verify-lotsize\repro.py，輸出存在 repro.out。組合模式（手續費 0.0002＋5bps，buy_cost=0.0007）app 股數／整股／零股：2454 0/0/40、3034 300/0/304、6196 300/0/332、0050 1600（lots=16.0）/1000/1613、2379 200/0/223、3545 3300/3000/3331
      - 本地重現：獨立候選模式 0050、2379、2454、3034、3545、6196 六筆交易全部是 shares=100.0、lots=1.0
      - 本地重現：停損 7% 時 2379 以 693.78 成交（跳動單位 1，往下取到單位為 693）、3034 508.71、6196 465.93（跳動單位 0.5）、2454 3813.0（跳動單位 5，應為 3810）；差距 0.08%～0.14%
      - 官方參考資料 csv/twse_stock_day_202608_202609.csv 的 2026-08-06 開盤價：2454=4100、3034=547、6196=501、0050=103.25、2379=746，與本地 parquet 相同
      - tick_check.py：官方 1520 個 OHLC 價格依 TickSizeModel 檢查，不在跳動單位上的是 0 個（tick_check.out）
      - https://www.managertoday.com.tw/articles/view/59023 原文：「1 張股票等於 1000 股」「從 1 股到 999 股，都可以透過零股交易買賣」
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所 原文：「一般股票以1,000股為一交易單位，未滿1,000股可透過零股交易」
      - frontend/src/router.tsx:82-121 沒有回測路由；除了 lib/api.ts 本身，沒有元件呼叫 strategyBacktestRun、backtestRun、startMining；lots 欄位在前端沒有被渲染
      - docs/features.md:42「本產品不提供因子挖掘、A 股連板分析、舊式策略回測平台或自動下單功能。」
      - data/backtest_results 是空目錄；data\job_store 的 5 筆 json 全部 status=failed，error="A 股日K修正管道已下线"
      - backend/app/main.py:424 app.include_router(backtest.router)：API 仍然掛載，但只能直接呼叫 HTTP 才用得到

### [P3 → P3] 統計口徑：年化固定用 252 日、Sharpe 未扣無風險利率、profit_factor 以百分比加總、mc_maxdd 把每筆交易當成全額資金（部分成立）

- **主張**：annual_return 與 Sharpe、Sortino 固定用 252（美股慣例），台股每年約 242～243 個交易日；Sharpe 以 rf=0 計算。profit_factor 用 pnl_pct 加總，而不是 TWD 金額。mc_maxdd 對每筆交易的 pnl_pct 做全額複利重抽樣，在組合模式下（每筆約占 1/max_positions）會誇大回撤。獨立候選（full）模式的 Sharpe 只取有出場的日子，再乘 √252。
- **正確的定義或數值**：年化因子依市場（台股約 243）或以實際日曆年數計算；Sharpe 註明 rf=0 或可設定；profit_factor 以金額計算；mc_maxdd 依實際部位比例或以日報酬重抽樣。
- **app 實際行為**：如上所述。
- **對使用者的影響**：年化報酬與 Sharpe 高估約 2～4%（相對值）；mc_maxdd 在組合模式下偏悲觀；這些都不會讓結論反轉，但跨市場比較時會有偏差。
- **修法建議**：把 periods_per_year 做成市場參數（台股 243）；在回傳中加上 rf 欄位；profit_factor 改用 pnl_amount；mc 模擬改對日報酬重抽樣。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:3145 annual_return = (1+TR)^(252/len) - 1
  - backend/app/backtest/engine.py:3149 sharpe = mean/std×√252（沒有扣 rf）
  - backend/app/backtest/engine.py:316-326、3151-3158 profit_factor 以 pnl_pct 計算
  - backend/app/backtest/engine.py:2846-2850、3179-3180 mc_maxdd 以 per-trade pnl 全額 cumprod
  - backend/app/backtest/engine.py:3030-3057 full 模式只取出場日的日均值再乘 √252
- **線上證據**：
  - https://www.ctee.com.tw/news/20251030700042-430201 2026 年台股「全年交易日243天」
  - https://web.stanford.edu/~wfsharpe/art/sr/sr.htm Sharpe ratio 定義為「the historic average differential return per unit of historic variability of the differential return」（相對基準或無風險資產的差額報酬）
  - https://docs.tradingmetrics.com/en/technical-analysis/trading-metrics/trade-specific-metrics/profit-factor（搜尋結果摘要）：「how many dollars you make for every dollar you lose」
- **本地計算證據**：
  - verify_engine.py Part 8：enriched 分區數 2024=242、2025=243 個交易日
  - verify_engine.py Part 7：同一條淨值曲線，annual 用 252 日為 0.5251，用 245 日為 0.5073；依公式用 243 日約 0.502。Sharpe 以 rf=1.7%、ddof=1 計算為 2.30，app 為 2.43。profit_factor 以百分比計為 1.79，以 TWD 計為 1.76
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：組合模式的統計口徑（engine.py:3145-3180）有以下四個問題，full 模式另有一個：

(1) annual_return、Sharpe、Sortino 固定用 252；台股 2024 年實際 242 日、2025 年 243 日（本地兩套分區資料一致，2026 年官方新聞也寫 243 日）。年化報酬的絕對值因此放大約 2.3%～3.9%（2025 全年樣本：app 0.1111，用 243 日為 0.1069）。Sharpe 放大 1.84%。報酬為負時是誇大虧損，不是「高估」。
(2) Sharpe 以 rf=0 計算，也沒有標註。這雖是 quantstats 等函式庫的常見預設，但以臺銀一年期固定利率 1.7% 計，影響遠大於 252 的問題：0.862 對 0.717；Sharpe 接近 0 時還會翻號（+0.082 對 -0.047）。
(3) profit_factor 以 pnl_pct 加總。組合模式下與以 TWD 金額計相差約 ±1%～9%，應改用 pnl_amount。但 full（獨立候選）模式每筆固定 100 股，pnl_amount 等同依股價加權（1.12 對 0.80），該模式應保留以百分比計（等權樣本）。
(4) mc_maxdd 把每筆 pnl_pct 當成全額資金連續複利。組合模式（每筆約 1/max_positions）下嚴重誇大回撤：2025 年 mp=10，p50 -0.645／p95 -0.862，實際 MDD 只有 -0.100，按部位縮放後為 -0.079／-0.148。mp=1 時兩者一致。
(5) full 模式的 Sharpe 只取出場日的平均「持有期報酬」再乘 √252。2025 年只有 111 個出場日，Sharpe 絕對值因此放大約 1.5 倍（-0.45 對 -0.30）。

以上都不影響使用者目前看到的畫面：前端沒有顯示這些統計，本地也沒有回測或 mining 紀錄。只有透過 API 或最佳化器使用時才會受影響，所以維持 P3。

注意：simulate()／_calc_stats 與 factor.py 用日曆年 365.25 年化，不在「固定 252」的範圍內。
    - 理由：結論：finding 對程式碼行為的五項描述全部屬實，稽核者的數字也能照公式重現。但「影響約 2～4%、不會讓結論反轉」低估了其中三項，profit_factor 的修法建議也不適用於 full 模式，所以判為部分確認。

驗證方式：我寫了自己的腳本（放在 scratchpad\audit\work\verify-statmetrics\）。本地 parquet 用 polars 唯讀讀取，把 app 的 simulate_market_matrix／simulate_independent_market_matrix（純記憶體計算，網路與存檔函式已先封鎖）套在自組面板上：自選股加 20 檔權值股，訊號用 MA5/20 金叉進場、死叉出場。第一輪樣本含 0050 在 2025-06-18 的分割（-74.8%），以及 2327 在 2025-08-25 的 -73.8% 未還原跳空，數字被扭曲，因此改用剔除 5 檔後的 24 檔乾淨樣本作為主要證據。跑完後 git status 與基準相同，data\ 近 30 分鐘沒有新檔。

(1) 交易日數：本地 taiwan/daily 與 kline_daily_enriched 的分區數都是 2024=242、2025=243；index_daily 另計 2025 年也是 243。官方 STOCK_DAY 參考資料 2026-08～09 的 38 個日期與本地完全一致。2024 年缺的 7/24、7/25、10/2、10/3、10/31 是颱風休市（依日期推定）。工商時報原文寫 2026 年「全年交易日243天」。
(2) 年化因子固定 252：2025 全年乾淨樣本（mp=10、243 個淨值點），total_return 0.1069，app 的 annual_return 0.1111。用 243 算是 0.1069，用日曆年算是 0.1076，相對放大 3.2%～3.9%。其他組合相對放大 2.3%～3.7%。Sharpe 只受 √(252/243) 影響，放大 1.84%，低於 finding 說的 2～4%。另外 252 放大的是「絕對值」：報酬為負時虧損被誇大，不是一律「高估」。
(3) rf=0：以臺銀一年期固定利率 1.700% 當 rf 並用 √243，2025 乾淨樣本的 Sharpe 從 0.862 降到 0.717（相對 +20%）。2024-2025 乾淨樣本從 +0.082 變成 -0.047，正負號翻轉。因此「不會讓結論反轉」不成立。不過 quantstats 的預設同樣是 rf=0、periods=252，這是常見慣例，問題主要在於沒有標註。
(4) profit_factor：組合模式乾淨樣本以百分比計與以 TWD 計相差 -1.0%～+9.0%（1.3443 對 1.3584、1.0268 對 1.0065、0.3447 對 0.3163）；稽核者的 1.79 對 1.76 同一量級。但 full（獨立候選）模式每筆固定 100 股（engine.py:1026-1044），TWD 加總等同依股價加權：2025 年以百分比計 1.1226、以 TWD 計 0.7960。所以該模式改用 pnl_amount 反而更失真，finding 的修法只適用於組合模式。
(5) mc_maxdd：這項確認，而且偏差遠大於「偏悲觀」。2025 年 mp=10：app 的 p50/p95 是 -0.645/-0.862，實際 MDD 只有 -0.100。按 position_pct 縮放後重抽，p50/p95 是 -0.079/-0.148；以日報酬重抽是 -0.098/-0.193。2024-2025 年 mp=10：app 的 p50 是 -0.863，實際 MDD 是 -0.246。mp=1（每筆約用全部資金）時，app 的 -0.283 和縮放版 -0.2827 一致，證明偏差完全來自忽略部位比例。
(6) full 模式 Sharpe：這項確認。2025 年只有 111／243 天有出場，app 的 -0.45 等於出場日平均報酬乘 √252（我算 -0.452）；改依實際出場日頻率年化是 -0.300，絕對值放大約 1.51 倍（√(252/111)）。而且每個樣本點是平均持有約 15 根 K 棒的報酬，不是日報酬。
(7) 範圍說明：_calc_stats（simulate 路徑，engine.py:2894-2898）與 factor.py:1182-1196 的年化都用日曆年 365.25，所以「固定用 252」只適用於組合模式與 full 模式的統計。

嚴重度維持 P3：前端 src 沒有任何頁面顯示 sharpe、annual_return、profit_factor、mc_maxdd，也沒有呼叫 backtestRun 或 mining API。本地 data/backtest_results 是空的，data/research/mining/runs 也沒有任何執行紀錄，依使用者情境目前沒有實際影響。只有透過 API 或最佳化器（optimizer.py:29-32 允許以 sharpe、profit_factor、mc_maxdd_p50/p95 為目標）使用時才會受影響。
    - 證據：
      - 程式碼：backend/app/backtest/engine.py:3145 annual_return = (1+TR)**(252/len(equity_values))-1；:3149 sharpe = mean/std*sqrt(252)，沒有 rf；:2808 _sortino_ratio(periods_per_year=252)，:3150 呼叫時沒有傳入參數
      - 程式碼：engine.py:316-326 _gross_profit_factor；:3151 pnls 取自 t.pnl_pct；:3158 _pf = _gross_profit_factor(wins, losses)；:3179-3180 以 pnls 呼叫 _mc_drawdown_percentiles；:2846-2848 samples → cumprod(1+samples)，沒有乘部位比例
      - 程式碼：engine.py:2083 組合模式 target_value = equity_before*max_exposure_pct/max_positions；engine.py:1026-1044 獨立模式固定 shares=100，所以 pnl_amount 等同依股價加權
      - 程式碼：engine.py:3035-3057 full 模式 daily_returns 只以出場日為鍵，sharpe = mean/std*sqrt(252)
      - 程式碼（範圍對照）：engine.py:2894-2898 的 _calc_stats 與 factor.py:1182-1196 都用日曆年 365.25 年化，與組合模式口徑不一致
      - 本地計算 p1_calendar.py（scratchpad\audit\work\verify-statmetrics\p1_calendar.py）：taiwan/daily 與 kline_daily_enriched 的分區數 2024=242、2025=243、2026=176；index_daily 的 2025=243；官方 0050 在 2026-08～09 的 38 個日期與本地一致（identical: True）；2024-07-24/25、10-02/03、10-31 本地不存在
      - 本地計算 p3_clean.py（24 檔、無分割跳空）2025 年 mp=10：total_return 0.1069；annual_return 依 app 為 0.1111、用 243 算為 0.1069、日曆年為 0.1076；sharpe 依 app 為 0.86（rf0、√243 算 0.847；rf=1.7%、√243 算 0.717）；PF 以百分比計 1.3443、以 TWD 計 1.3584；mc_maxdd 依 app p50 -0.6449／p95 -0.8623，實際 MDD -0.1004，按部位縮放重抽 -0.0789／-0.1478，日報酬重抽 -0.0983／-0.1928
      - 本地計算 p3_clean.py 2024-2025 年 mp=10：sharpe rf0 為 +0.082、rf=1.7% 為 -0.047（正負號翻轉）；mc_maxdd 依 app p50 -0.8626，實際 MDD -0.246；mp=1：app 的 mc p50 -0.45 與縮放版 -0.4505 相同
      - 本地計算 full 模式 2025 年：出場日 111／243 個交易日；app sharpe -0.45 = 我算的 -0.452（√252），依出場頻率 √111 年化為 -0.300；PF 以百分比計 1.1226、以 TWD（100 股名目）計 0.7960
      - 本地計算：由 app 的 0.5251 反推 38 個淨值點的 TR=0.06571，用 245 日得 0.5073、243 日得 0.5023，與 finding 的 0.5251／0.5073／約 0.502 一致；√(252/243)-1=1.84%
      - 線上：https://www.ctee.com.tw/news/20251030700042-430201 原文「全年交易日243天」（2026 年）
      - 線上：https://web.stanford.edu/~wfsharpe/art/sr/sr.htm 「the historic average differential return per unit of historic variability of the differential return」；differential return = 基金報酬減基準報酬
      - 線上：https://docs.tradingmetrics.com/en/technical-analysis/trading-metrics/trade-specific-metrics/profit-factor 「Profit Factor = Gross Profit / Gross Loss」，Gross Profit 定義為「The total amount of money made from winning trades」
      - 線上：https://raw.githubusercontent.com/ranaroussi/quantstats/main/quantstats/stats.py 中 def sharpe(returns, rf: float = 0.0, periods: int = 252, ...)；cagr 的 years = len(returns) / periods，可見 rf=0、252 為常見函式庫預設
      - 線上：https://rate.bot.com.tw/twd 臺灣銀行一年期定期存款固定利率「1.700」，實施日期 2024/08/01（rf=1.7% 的依據）
      - 使用範圍：frontend/src 沒有任何 .tsx 讀取 sharpe、annual_return、profit_factor、mc_maxdd，也沒有頁面呼叫 backtestRun 或 mining API；data/backtest_results 為空；data/research/mining/runs 沒有任何執行紀錄；optimizer.py:29-32 把 sharpe、profit_factor、mc_maxdd_p50/p95 列為可選目標

### [P3 → P3] legacy simulate()／_calc_stats 是沒有正式呼叫端的舊路徑，統計公式錯誤；台股回測測試只覆蓋這條路徑（部分成立）

- **主張**：engine.simulate() 沒有正式呼叫端（grep '.simulate(' 只命中 tests/test_taiwan_backtest.py:171）。它的 _calc_stats 有三個錯誤：把所有交易依序全額複利算 total_return（並行持倉會被嚴重高估）；交易清單依 symbol 排序而非時間排序，max_drawdown 因此不是時間序；逐筆 Sharpe 再乘 √252。tests/test_taiwan_backtest.py 以這條路徑驗證 TaiwanMarketProfile，給人「台股成本與張數已接上正式回測」的錯覺。
- **正確的定義或數值**：刪除 legacy 路徑，或讓台股測試直接走 simulate_market_matrix（正式路徑）。
- **app 實際行為**：測試覆蓋的是沒有正式呼叫端的路徑，正式路徑的台股成本、張數、漲跌停反而沒有被測試。
- **對使用者的影響**：不直接影響使用者，但上述 P1／P2 問題因此沒有被測試抓到。
- **修法建議**：把 TestTaiwan 系列改為呼叫 simulate_market_matrix，並斷言 lot=1000、ETF 稅率、TWT49U 漲跌停鎖死；移除或標示 legacy simulate()。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/engine.py:723-785 逐 symbol 迴圈 append 交易
  - backend/app/backtest/engine.py:2886-2924 total_return 以各筆複乘、max_dd 依清單順序、sharpe 為逐筆×√252
  - backend/tests/test_taiwan_backtest.py:142-171 以 engine.simulate(market_profile=profile) 測試
- **線上證據**：
  - https://web.stanford.edu/~wfsharpe/art/sr/sr.htm Sharpe 以固定期間的報酬序列計算，再依期數平方根年化；逐筆交易並不是等長期間
- **本地計算證據**：
  - grep -rn '\.simulate(' backend/app → 無結果；backend/tests 只有 test_taiwan_backtest.py:171
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：engine.simulate()（engine.py:670）在 app 內沒有正式呼叫端，唯一呼叫在 tests/test_taiwan_backtest.py:171；它所用的 _calc_stats（engine.py:2871）在 app 內也只被 simulate 呼叫，但 tests/backtest/test_robustness_metrics.py 直接測試它。_calc_stats 有三個已重現的統計錯誤：
(1) total_return 把所有交易依序全額複利。並行持倉時報酬與回撤的幅度都被放大：10 檔同時各賺 10%，legacy 得出 159.37%，正式路徑為 10%；自選 9 檔的 MA5/MA20 策略，legacy 報酬 107.45%、回撤 -75.85%，正式路徑為 19.15% 與 -19.5%。
(2) 交易清單依 np.unique 的 symbol 順序排列，max_drawdown 因此不是時間序。三筆交易的例子 legacy 得 -30%，時間序與正式路徑都是 -51%；自選 9 檔若依出場日重排，max_dd 由 -75.85% 變成 -94.43%。
(3) 逐筆 Sharpe 乘 √252。平均持有 16.1 天的交易得出 1.43，依每年筆數年化應為 0.79，正式路徑的日報酬 Sharpe 為 0.6。
simulate() 產出的 stats 也和它自己的 equity_curve、drawdown_curve 互相矛盾。
tests/test_taiwan_backtest.py 唯一實際跑回測的測試，透過 MatcherConfig(market_profile=profile) 走這條舊路徑，而且只斷言曲線長度與終值大於 0，沒有驗證任何台股成本或張數。沒有任何測試讓正式路徑 simulate_market_matrix 帶入 TaiwanMarketProfile 驗證手續費折扣、證交稅或 1000 股張數；app 內也沒有任何程式設定 market_profile。
但正式撮合的漲停不買、跌停不賣有測試（test_engine_portfolio.py:71-127），台股 limit_up_locked 在 matrix 層的推導也有測試（test_price_limits.py:649-693），所以「正式路徑的漲跌停沒有被測試」不成立；沒測到的只有 TWT49U 相關的鎖死，以及帶台股 profile 的端到端回測。
另外，simulate() 帶入 profile 後，ETF 仍按 0.3% 課證交稅，也沒有張數限制。其中 ETF 稅率問題來自兩條路徑共用的 sell_cost_pct（engine.py:95-101），不限於舊路徑。
建議：刪除或明確標示舊的 simulate／_calc_stats，並把台股回測測試改走 simulate_market_matrix 加上 TaiwanMarketProfile。
    - 理由：主張的核心都重現了。simulate() 在 app 內沒有任何呼叫端；_calc_stats 的三個統計錯誤也逐一用數據重現。資料來源有兩種：人工構造的 panel，以及本地 parquet 中自選 9 檔 2024-01-02～2026-09-23 的 5944 列。三個錯誤是：各筆交易全額複利、交易清單依 symbol 排序而非時間、逐筆 Sharpe 乘 √252。三項都與正式路徑 simulate_market_matrix 的結果明顯不同，而且和 simulate() 自己產出的 equity_curve／drawdown_curve 也互相矛盾。test_taiwan_backtest.py 唯一實際跑回測的測試走的正是 simulate()，而且只斷言曲線長度等於 n、終值大於 0，完全沒驗證成本或張數。

以下三個次要敘述不精確：
(1) profile 是經由 MatcherConfig(market_profile=profile) 傳入（test:157），不是 simulate 的參數；simulate 簽名（engine.py:670-678）沒有 market_profile。
(2)「正式路徑的漲跌停沒有被測試」不成立。正式撮合的漲停不買、跌停不賣有測試：test_engine_portfolio.py:71-90 斷言 buy_limit_up==1，93-127 斷言 sell_limit_down==1。台股 limit_up_locked 在 matrix 層的推導也有測試（test_price_limits.py:649-693，2330.TWSE 鎖漲停、無漲跌幅 ETF 不鎖）。真正沒測到的是以 TaiwanMarketProfile 端到端跑正式路徑，以及 TWT49U 相關的鎖死。
(3) 所謂「並行持倉被高估」，實際上是報酬與回撤的幅度都被放大，不只高估獲利。

另有 finding 沒提到的面向：_calc_stats 在 tests/backtest/test_robustness_metrics.py:125,136,200 被直接測試，等於把舊公式釘死。simulate() 帶入 TaiwanMarketProfile 後，0050 ETF 仍按一般股票 0.3% 課證交稅（sell_cost_pct 只讀 ordinary_stock_rate，engine.py:97-99）；也完全沒有張數限制，50,000 元照樣成交 1 張 100 元的 0050（1 張需 100,000 元）。不過 sell_cost_pct 是兩條路徑共用的，ETF 稅率問題並不限於舊路徑。此外，app 內沒有任何程式設定 market_profile（grep 只命中 engine.py 自身），所以正式路徑在生產環境也從未套用台股 profile。嚴重度維持 P3：沒有正式呼叫端，屬於維護與測試覆蓋問題。
    - 證據：
      - 無呼叫端：Grep '\bsimulate\b' 掃 backend，只命中 backend/app/backtest/engine.py:670（定義）、tests\test_taiwan_backtest.py:171，以及 app\backtest\strategy.py:1536 的 timing_ms 字典鍵名 'simulate'（不是呼叫）。正式呼叫的是 strategy.py:1520 simulate_independent_market_matrix 與 strategy.py:1529 simulate_market_matrix。
      - _calc_stats 在 app 內唯一的呼叫在 engine.py:796（simulate 內部）；正式路徑改用 _calc_portfolio_stats_from_values，在 engine.py:2173、2753、3131-3149 以淨值曲線計算 total_return、max_dd 與日報酬 Sharpe。另外 tests\backtest\test_robustness_metrics.py:125,136,200 直接測試 _calc_stats（finding 沒提到）。
      - 程式碼：engine.py:721 `unique_symbols = np.unique(panel_symbols)`，723-785 逐 symbol append，所以交易清單依 symbol 排序；2886-2891 逐筆複利（原註解自承『用各筆复乘作为近似』）；2913-2921 依清單順序算 max_dd；2924 `np.mean(pnls)/np.std(pnls)*np.sqrt(252)`。
      - 重現腳本：AUDIT_SCRATCH/work/verify-legacy-sim/repro.py；輸出存在同目錄 out.txt。執行指令：cd backend && uv run python repro.py，exit=0。
      - 情境 A（10 檔同時持有，每筆 +10%，零成本）：legacy _calc_stats total_return=1.5937（理論值 1.1^10-1=1.5937）；legacy 自己的 equity_curve 終值報酬=0.10；正式 simulate_market_matrix total_return=0.1。finding 所說的並行高估成立。
      - 情境 B（2454、3034、6196 三筆不重疊交易，時間順序為 -30%、-30%、+100%）：legacy 交易清單順序為 2454(2024-01-04)→3034(01-07)→6196(01-01)，_calc_stats max_drawdown=-0.30；legacy 自己的 drawdown_curve 最小值=-0.51；正式路徑 max_drawdown=-0.51。依 symbol 排序導致 max_dd 不是時間序，成立。
      - 情境 C（本地 parquet，自選 9 檔，MA5/MA20 交叉，零成本）：legacy total_return=1.0745、max_dd=-0.7585、sharpe=1.43、n_trades=207；正式路徑 total_return=0.1915、max_dd=-0.195、sharpe=0.6、n_trades=194。交易清單 sorted by symbol=True、sorted by exit_date=False；同一公式改依出場日排序，max_dd 變成 -0.9443。逐筆 mean/std=0.0903，×√252=1.43，×√(每年 76.0 筆)=0.79，平均持有 16.1 天。legacy 自己的 equity_curve 日報酬 Sharpe 為 0.9，與 stats 的 1.43 自相矛盾。
      - 情境 D（legacy simulate 帶入 TaiwanMarketProfile）：0050 ETF 的 pnl_pct=0.09529，即 0.10 扣掉 buy 0.000855 與 sell 0.003855，賣方稅率用了 0.3%；若依 ETF 0.1% 應為 0.09729。initial_capital=50,000 仍成交 1 筆，但 1 張 0050（100 元×1000 股）需 100,000 元，可見沒有張數限制。
      - 測試內容：tests\test_taiwan_backtest.py:151-158 以 MatcherConfig(market_profile=profile) 傳入 profile，171-176 呼叫 engine.simulate，178-181 只斷言 result 不為 None、len(equity_curve)==n、終值 >0；189-227 的 ETF／TPEx 測試直接呼叫 profile 物件的方法，不經過任何回測引擎。
      - 正式路徑的漲跌停其實有測試：tests\backtest\test_engine_portfolio.py:71-90（simulate_portfolio，buy_limit_up==1）、93-127（sell_limit_down==1、blocked_exit_days==1）；tests\test_price_limits.py:649-693 驗證 2330.TWSE 的 limit_up_locked==1、無漲跌幅 ETF 為 0。Grep 'market_profile' 掃 backend\tests：只有 test_taiwan_backtest.py 傳入 profile，沒有任何測試讓 simulate_market_matrix 帶 profile；掃 backend\app 只命中 engine.py:76-99、2536-2537、2662-2663，沒有程式設定 market_profile。
      - 線上來源 https://web.stanford.edu/~wfsharpe/art/sr/sr.htm（Sharpe, 1994）的原文：『The Sharpe Ratio is not independent of the time period over which it is measured.』『To maximize information content, it is usually desirable to measure risks and returns using fairly short (e.g. monthly) periods. For purposes of standardization it is then desirable to annualize the results.』另有式 (10)：T 期 Sharpe 等於單期 Sharpe×√T。因此年化因子必須對應等長期間的期數，平均持有 16 天的逐筆報酬不能乘 √252。
      - 唯讀確認：執行前後 git status --short 相同，都只有既有的 daily_update.py、pnpm-lock.yaml、vite.config.d.ts 三項修改。

## 確認正確的項目

- **訊號日與成交日對齊（open_t+1：t 日訊號、t+1 開盤成交；close_t：t 日收盤成交），沒有未來函數**：把官方 STOCK_DAY 日 K（2454、3034、6196、3545、0050、00878，2026-08-03～09-23，38 日）餵給 build_market_matrix 與 BacktestEngine.simulate_market_matrix。8/05 設買訊、8/21 設賣訊，逐筆比對成交日與成交價。open_t+1 六檔都在 8/06 開盤買、8/24 開盤賣，close_t 六檔都在 8/05 收盤買、8/21 收盤賣，全部 OK。程式碼：matrix.py:2853-2877（_delay_signal_matrix 移到下一個有資料的 bar）、engine.py:1747-1748
  - 來源：backend/app/backtest/matrix.py:2853
  - 來源：backend/app/backtest/engine.py:1747
  - 來源：scratchpad\audit\work\A11\verify_engine.out（Part 4 各行 check ... -> OK）
- **不做當日沖銷（買進當天不賣出）與 T+2 交割的關係**：讀程式碼：風控出場跳過 entry_date==當日的部位（engine.py:1998）；計畫出場在建倉之前處理（engine.py:2035-2052 早於 2054-2141）。所以永遠不會當日買賣。台股可以現股當沖（證交稅條例第2-2條），但日線回測採保守的現股隔日模式屬合理近似。T+2 只影響交割，不限制隔日賣出，engine 也沒有錯把 T+2 當成持有限制。0.15% 當沖稅率因此不適用。
  - 來源：https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2-2條：「同一帳戶於同一營業日現款買進與現券賣出同種類同數量之上市或上櫃股票，於出賣時，按每次交易成交價格依千分之一點五稅率課徵」
  - 來源：https://rich01.com/what-is-settlement-period-t-plus-2-mean/ 「交易日後第二天的早上10點，投資人會支付或收到股票或錢」
  - 來源：backend/app/backtest/engine.py:1998
- **組合統計：total_return、annual_return（252 日慣例）、max_drawdown、sharpe（rf=0、母體標準差）、sortino、calmar、win_rate、payoff_ratio**：用官方日 K 加隨機訊號（seed 7）跑出 30 筆交易、38 日淨值，再從 equity_curve 獨立重算。結果與 app 完全一致：total_return 0.0657／0.0657、annual 0.5251／0.5251、maxdd −0.0477／−0.0477、sharpe 2.43／2.43、sortino 3.78／3.78、calmar 11.01／11.01、win_rate 0.5667／0.5667。公式在 engine.py:3141-3168
  - 來源：backend/app/backtest/engine.py:3141
  - 來源：scratchpad\audit\work\A11\verify_engine.out Part 7
- **Sharpe 用母體標準差（除以 T）**：對照 Sharpe (1994)：原文說以 T 為分母的歷史標準差「can generally be used instead」。app 用 np.std(ddof=0)，本地重算 ddof=1 為 2.39，對比 2.43
  - 來源：https://web.stanford.edu/~wfsharpe/art/sr/sr.htm 「the standard deviation of the historic data (in which the denominator is T rather than T-1) can generally be used instead」
  - 來源：backend/app/backtest/engine.py:3149
- **Sortino 下行偏差的分母用全部樣本數（MAR=0）**：讀程式碼 engine.py:2815-2823（downside=min(r,0)，對全部樣本取平方平均），對照權威定義；本地重算 3.78 與 app 相同
  - 來源：https://en.wikipedia.org/wiki/Sortino_ratio（搜尋結果摘要：You divide by the total number of observations (N), not just the number of negative returns）
  - 來源：backend/app/backtest/engine.py:2815
- **minute_trigger.py 的分鐘級賣出觸發線只用當日已知資料（ma5／ma10／ma20 跌破、5/20 死叉）**：代數驗證：MA5(P)=(sum4+P)/5 < P 等價於 P < sum4/4，而 (5·ma5−close)/4 正好是 sum4/4；ma10 同理為 sum9/9；死叉 (sum4+P)/5 < (sum19+P)/20 等價於 P < (sum19−4·sum4)/3，與程式碼一致
  - 來源：backend/app/backtest/minute_trigger.py:33-46
- **市場環境過濾採 T-1 對齊，只作用在進場**：讀程式碼 strategy.py:1794-1830 → regime_alignment.build_regime_filter_mask 呼叫 align_regime_t_minus_one（regime_alignment.py:151）；strategy.py:1236-1247 只 AND 到 entry_time_mask
  - 來源：backend/app/backtest/regime_alignment.py:126-166
  - 來源：backend/app/backtest/strategy.py:1236
- **步進優化的訓練期與測試期不重疊（test_start = train_end + 1 天）；optimizer 目標值白名單與方向**：讀程式碼 walkforward.py:48-53、optimizer.py:24-32
  - 來源：backend/app/backtest/walkforward.py:48
  - 來源：backend/app/backtest/optimizer.py:24
- **缺歷史股本時，換手率與市值欄位 fail-closed（不拿最新股本回填歷史）**：唯讀呼叫 load_market_data_matrix_from_parquet（cache_root=None）：要求 turnover_rate 時拋 'matrix turnover_rate requires float_shares'；要求 total_shares 時拋 'fields unavailable'。掃描 661 個 enriched 分區，只有 2026-09-16 起的 6 個分區有 turnover_rate。符合 CONTRIBUTING 3.4 不用最新股本推算歷史的規定
  - 來源：backend/app/backtest/matrix.py:1434-1447
  - 來源：scratchpad\audit\work\A11\verify_matrix_load.out
- **進場評分取訊號當時的 score（不是成交日的 score）**：讀程式碼 engine.py:36-40：_matrix_entry_score 用 entry_signal_time 取 score
  - 來源：backend/app/backtest/engine.py:36
- **停損／移動停損／停利的觸價成交（開盤已穿越用開盤價，否則用觸價線），以及出場優先序（風控 → 訊號 → 持有期滿 → 期末）**：讀程式碼 engine.py:2007-2052；以官方 2379、00878 日 K 實跑 3% 停損。00878 開盤 32.76 ≤ 線 32.85，成交在開盤價；2379 最低 680 ≤ 線 691.61，成交在觸價線 691.61，都符合設計
  - 來源：backend/app/backtest/engine.py:2007
  - 來源：scratchpad\audit\work\A11\verify_exdiv.out
- **max_hold_days 與 hold_days 以交易日（矩陣的時間軸）計算**：讀程式碼 engine.py:1993-1994、2047：每個矩陣 bar 加 1，矩陣時間軸就是交易日分區
  - 來源：backend/app/backtest/engine.py:1993
- **台股規則模組 app/taiwan/market_rules.py 的常數：證交稅（股票 0.3%、ETF 0.1%、當沖 0.15% 至 2027-12-31、債券 ETF 0% 至 2026-12-31）、整股 1000 股、升降單位（股票 6 級、ETF 2 級）、漲跌停取整方向**：(a) 對照財政部證交稅條例原文；(b) 用 PriceLimitModel 算 TWT49U 的官方漲跌停：一般股 781 筆相符 758 筆，ETF 120 筆全部相符（不符的 23 筆是官方特殊除權基準，例如 2313 的官方漲停為 285，已超過 +10%）；(c) SecuritiesTaxModel 輸出 0.003／0.001／0.001／0.0／0.0015、2027-12-31、2026-12-31。注意：這個模組本身正確，但沒有接進正式回測（見 findings）
  - 來源：https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079 第2條「公司發行之股票…徵千分之三。二、公司債及其他經政府核准之有價證券徵千分之一」；第2-1條標題「暫停徵公司債、金融債券及債券指數股票型基金受益憑證證交稅之年限」至一百十五年十二月三十一日；第2-2條至一百十六年十二月三十一日
  - 來源：https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html 「理論漲停為 44.66 元、跌停為 36.54 元…合法價格要調整為 44.65 與 36.55 元」
  - 來源：backend/app/taiwan/market_rules.py:101-109
  - 來源：scratchpad\audit\work\A11\verify_pricelimitmodel.out
- **profit_factor 的定義（總獲利 ÷ 總虧損），以及 payoff_ratio（平均獲利 ÷ 平均虧損）分開計算**：讀程式碼 engine.py:316-326，定義正確；計量單位的問題另列於 findings
  - 來源：https://docs.tradingmetrics.com/en/technical-analysis/trading-metrics/trade-specific-metrics/profit-factor（搜尋結果摘要：Profit Factor = Total Gross Profit / Total Gross Loss）
  - 來源：backend/app/backtest/engine.py:316
- **停牌或無量一字價判為不可交易（tradable）**：讀程式碼 matrix.py:2780-2797：volume≤0 且 OHLC 價差在容差內，視為停牌
  - 來源：backend/app/backtest/matrix.py:2780

## 無法驗證的項目

- **/api/backtest/run（vectorbt 路徑，services/backtest.py）的統計與成本**：本機沒有安裝 vectorbt（`uv run python -c "import importlib.util;...find_spec('vectorbt')"` 輸出 vectorbt False），端點會回 503，無法實跑。可以確定的是它的預設也是 fees_pct 0.0002（services/backtest.py:85，註解「万二佣金」）
- **minute_fill 分鐘精確成交價與官方分鐘成交資料的比對**：data/kline_minute 只有 4 個交易日的自錄分鐘 K，參考資料也沒有官方分鐘資料；只能用合成資料驗證邏輯（見 findings 的未來函數項）
- **與第三方公開回測結果做三方比對**：沒有任何公開網站以相同策略、相同參數公布台股回測數值，無法比對。統計公式改用權威定義，搭配本地獨立重算驗證
- **上櫃（TPEx）標的的回測行為**：kline_daily_enriched 的 2026-09-23 分區 1323 檔全部是 .TWSE，本地資料沒有可回測的上櫃標的
- **最低手續費 20 元與券商折扣的實際影響**：這些屬券商設定（contract v1 明訂沒有法定預設）。app 完全沒有最低手續費模型；以預設 100 萬本金、10 檔計，每筆約 10 萬元，手續費約 142 元，遠高於 20 元，所以影響只在零股或小額時才出現，無法用官方資料量化
- **regime（市場環境）歷史資料本身的正確性**：屬其他稽核領域；這裡只驗證了 T-1 對齊的方式
- **TWT49U 中 23 筆官方漲跌停超過 ±10% 的特殊除權案例（如 2313、4989、7610）**：官方基準價的算法（例如含權值另計）在參考資料中沒有說明，屬漲跌停稽核領域，不影響本領域的結論
