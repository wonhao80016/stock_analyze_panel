# A08-strategies-trend｜策略合理性（趨勢與突破類）

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 2 | 10 | 0 | 0 | 0 | 14 | 7 |

## 稽核者摘要

結論：7 支策略的程式邏輯與 META 描述大致相符。我依教科書定義獨立重寫了一次，拿 2026-09-23 的 strategy_cache 比對，7 支的選股集合完全一致（166/11/10/12/6/19/48 檔，差異 0）。9/3 到 9/23 共 15 個交易日的全市場逐日比對中，boll、ma_golden、trend_breakout 100% 一致，其餘差異都已找到原因。評分重算誤差為 0。周轉率等於「成交股數／官方已發行普通股數 ×100」，122 檔全部吻合。MA5/20/60 與 Yahoo（第三方）三方比對結果完全相同。問題集中在資料與框架層，而且會直接改變選股結果：(1) P1：9/23 的 16:30 快照在 TPEx 抓取失敗時靜默吞掉錯誤，只寫入上市資料的分區被當成完整，21:40 的補跑因為「日期已存在」而跳過，所以 9/23 的選股 0 檔上櫃（9/22 上櫃約占 29%），與 docs/radar-strategies-validation.md:130「不會用殘缺資料計算」的說法矛盾。(2) P2：consolidation_breakout 與 swing 把時間軸 rolling_max/min（遇 NaN 就傳染）和 valid_shift（跳過 NaN）混用。只要缺一根 K 棒，就會 20 根內完全不觸發，之後再用過期區間產生假突破。實測上櫃 9/16 缺檔讓 9/17–9/22 少了 30 檔整理突破；6176 在 9/18 則用 5 週前的區間高點 89.5（實際應為 108）觸發假訊號。(3) P2：台股全程使用未還原價（142 萬列中沒有任何一列經過還原）。以官方 TWT49U 的 956 次除權息為樣本，只因除息缺口而產生的假訊號有：MA20 跌破 221 次、跌破布林下軌 219 次、MA5/20 死叉 62 次、20 日動能轉負 225 次。自選的 00878 在 8/18 出現假的 MA20 跌破；若改用還原價，9/23 的均線多頭會多出 18 檔，大多是高息 ETF。(4) P2：單獨執行 macd_golden（run_preset）只載入 61 根做 EMA 暖機，15 天內約 292 筆選股中有 53 筆與全歷史 MACD 不同，而且這個結果會覆寫快取。(5) P2：ETF 沒有 turnover_rate，使 bullish_alignment 的 44 檔 ETF 評分被強制為 0（0050 排第 126、00878 排第 143）。另有數項 P3：exclude_new_days 設了但沒有實作、布林採樣本標準差、「60日新高」其實是收盤價口徑且 MA60 條件多餘、整理突破的評分方向與「低檔」前提相反、排名前段的突破股多為漲停鎖死、訊號標籤錯誤、成交量門檻隨股價而變。網路請求共 15 次（WebFetch 6、WebSearch 6、Yahoo chart API 3），沒有連證交所或櫃買中心；腳本與中間結果都在 AUDIT_SCRATCH/work/A08-strategies-trend/。範圍外的旁證（未追查）：9/23 的 enriched 中，2104、3653、6526、6838 的 signal_limit_up=True，但 consecutive_limit_ups=0。

## 問題清單

### [P1 → P1] 9/23 的策略結果完全不含上櫃股：TPEx 快照失敗被吞掉，只有上市資料的分區被當成完整日，而且不會重抓（確認成立）

- **主張**：snapshot_provider.fetch_date 在 TPEx 抓取失敗時只記 warning，回傳只有上市資料的 frame；refresh_dates 照樣寫入分區，之後的排程看到『日期已存在』就跳過。結果 9/23（以及 9/16）整天缺上櫃資料，當晚 run_all 的 7 支策略 0 檔上櫃。
- **正確的定義或數值**：任一市場的快照失敗時，這個日期應視為未完成（不寫入，或標記部分完成並在下一班重抓）；策略頁應拒絕或標示不完整的基準日。
- **app 實際行為**：只有上市資料的分區被當成完整；9/23 的策略快取少了約 1000 檔上櫃股，畫面上看不出來。
- **對使用者的影響**：使用者看到的『策略當日推薦』漏掉整個上櫃市場（9/22 時上櫃約占三成選股），而且沒有提示；9/16 也有同樣的缺口（該日的 production log 已輪替，原因無法確認）。
- **修法建議**：fetch_date 在任一市場失敗時拋例外，或回傳『部分完成』旗標；refresh_dates 對部分完成的日期不列入 available，下一班自動重抓；並對 9/16、9/23 做一次 force 回補。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:112-118（TPEx 例外只 logger.warning）
  - backend/app/taiwan/providers/snapshot_provider.py:120-124（只要有任一個 frame 就回傳）
  - backend/app/taiwan/daily_refresh.py:167-176（cur in available 就跳過；非空就 write_batch）
  - backend/app/jobs/daily_pipeline.py:169（只擋 daily.status=='failed'）
  - backend/app/jobs/daily_pipeline.py:193-195（註解宣稱 21:40 是第二次機會，但已落盤的日期會被略過）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - backend.log：『2026-09-23 16:30:05 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body』
  - tpex_count.py：taiwan/daily 與 enriched 在 2026-09-16、2026-09-23 的 TPEX 列數都是 0，其他日約 1000
  - strategy_cache.json（as_of 2026-09-23）：7 支策略 TPEX 0 檔；app_run.py 同一程式在 9/22 的 TPEX 占比：bullish 69/237、trend 8/25、macd 9/35、boll 25/85
  - docs/radar-strategies-validation.md:130 宣稱『不會用殘缺資料計算』，實際並非如此
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：OfficialDailySnapshotAdapter.fetch_date（snapshot_provider.py:112-124）遇到 TPEx 失敗只記 warning，照樣回傳只有上市資料的 frame；refresh_dates（daily_refresh.py:172-177）把它當成完整日寫入並回報 success。之後 run_update 的日期判斷只看 max(date)（daily_update.py:282-286、124-147），所以 21:40 那班與往後各班都不會再抓這一天，中間缺口（9/16）也永遠不回補；get_freshness 仍回報 is_fully_current=True。實際後果有兩個。第一，9/16 與 9/23 的 daily 和 enriched 分區都是 TPEX 0 列，as_of 2026-09-23 的策略快取裡全部 20 支策略都是 0 檔上櫃（不只 7 支），畫面上沒有任何提示；正常日上櫃約占三分之一的訊號命中。第二，缺口會污染隔日以後的上櫃指標：例如 6488.TPEX 在 9/17 的 prev_close 被存成 9/15 收盤 914，change_pct 是 −0.22%，官方數據則是 937→912、約 −2.67%；MA、布林、N 日高低點的視窗也都跨過缺口，在 force 回補之前一直存在。使用者的自選 9 檔都是上市股、監控規則為 0 條，所以自選頁與告警不受影響，受影響的是策略推薦卡與全市場選股。
    - 理由：這條 finding 的主幹我逐項親自驗證過，都成立。程式碼層面：snapshot_provider.fetch_date 在 TPEx 失敗時只記 warning，只要還有任一個 frame 就回傳；refresh_dates 看到非空 frame 就寫入分區，並把該日記成 dates_fetched、狀態 success。正確的規則是：台股全市場日資料由兩個獨立市場組成，集中市場（TWSE 上市）加上店頭市場（TPEx 上櫃）；TPEx 是獨立營運的 OTC 市場，週一至週五開市。官方參考資料顯示 9/16、9/23 上櫃個股都有成交，所以這兩天少了 TPEx 資料就是不完整的交易日，不能當成完整分區。

實際資料也對得上：taiwan/daily 與 enriched 只有 9/16、9/23 的 TPEX 列數是 0，其他日約 1000 列。backend.log 9/23 16:30 那班記錄了 TPEx 失敗，daily 狀態卻是 success；21:40 那班完全沒有對 MI_INDEX 或 dailyQuotes 發出請求，還回報 is_fully_current=True。strategy_cache.json（as_of 2026-09-23，mtime 21:40）裡 TPEX 的選股數是 0，而且不只 finding 說的 7 支：全部 20 支策略都是 0，其中 13 支有選股。

需要修正的細節有三點：
- 不重抓的直接原因不在 refresh_dates 的 `cur in available` 檢查。真正原因在更早的 run_update：d_max 已經大於等於 target，resolve_missing_date_range 就回傳 None，於是直接標成 "Daily store already current"，根本沒有呼叫 refresh_dates；而且這個函式只看 d_max 之後的日期，所以中間的缺口（9/16）永遠不會被補。
- 影響範圍比 finding 寫的大：20 支策略全部 0 檔上櫃。
- finding 沒提到的新面向是缺口會污染之後的上櫃指標。以 6488.TPEX 為例，9/17 的 prev_close 被存成 9/15 的收盤 914，change_pct 是 −0.22%；官方數據是 9/16 收盤 937、9/17 收盤 912，漲跌 −25（約 −2.67%）。MA、布林、N 日高低點的視窗也都跨過缺口。這種污染在 force 回補之前會一直存在。

嚴重度維持 P1，理由如下：
- 核心的全市場選股靜默漏掉約 43% 的標的；在 9/22 的 enriched 裡，上櫃約占三分之一的訊號命中。
- 違反 CONTRIBUTING.md:285「部分寫入時不得產生錯誤狀態」的規定。
- 資料可以用 force 回補，不算不可逆的破壞，所以不到 P0。

在使用者情境下的影響：自選 9 檔全部是 .TWSE，監控規則是 0 條，所以自選頁與告警不受影響。實際受影響的是「策略當日推薦」卡片與全市場選股，而且畫面上看不到任何提示。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-118：TPEx 例外只 logger.warning；:120-124：frames 非空就 concat 回傳，沒有任何部分失敗的旗標
      - backend/app/taiwan/daily_refresh.py:172-177：df 非空就 write_batch 並 dates_fetched += 1；:178-179：兩個市場都失敗時回傳空 frame，只算 dates_skipped，不列入 failed_dates
      - backend/app/taiwan/daily_update.py:282-286：d_max 大於等於 target 時直接標成 'Daily store already current'，不呼叫 refresh_dates；:124-147：resolve_missing_date_range 只看 (d_max, target]，中間的缺口永遠不回補
      - backend/app/taiwan/daily_update.py:178-224：get_freshness 只比對 max(date)，缺整個市場的分區仍判成 current
      - backend/app/jobs/daily_pipeline.py:169：只擋 result.daily.status == 'failed'；:194-195 的註解宣稱 21:40 是「第二次機會」
      - backend/app/repository.py:1416-1427：taiwan_official_latest_date 取 available_dates()[-1]，沒有檢查完整度；backend\app\services\screener.py:426 以此作為選股基準日
      - data/backend.log:37258『2026-09-23 16:30:05 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)』；:37265『daily=success ... is_fully_current=False』；:37360『strategy cache refreshed (daily+institutional): as_of=2026-09-23』
      - data/backend.log:45470-45478：21:40 那班沒有 MI_INDEX 或 dailyQuotes 請求，結果是『daily=success ... margin=success, is_fully_current=True』，接著 :45572 重算策略快取
      - 本地計算（scratchpad\audit\work\verify_tpex_gap\count.py，uv run python）：taiwan/daily 9/16 為 TWSE 1330／TPEX 0，9/23 為 TWSE 1330／TPEX 0，其他日 TPEX 1005～1009；kline_daily_enriched 9/16 與 9/23 的 TPEX 也是 0，其他日 978～992
      - 本地計算（cache.py）：strategy_cache.json 的 as_of 為 2026-09-23、mtime 為 2026-09-23 21:40:24；20 支策略的 TPEX 選股全部是 0（例如 bullish_alignment 166/0、boll_breakout 48/0、low_volatility_leader 135/0、trend_breakout 12/0）
      - 本地計算：9/22 enriched 各市場訊號數（TPEX 對 TWSE）：signal_macd_golden 48:99、signal_ma20_breakout 36:80、signal_boll_breakout_upper 62:125、均線多頭排列 121:240，顯示正常日上櫃約占三分之一的命中
      - 新面向：enriched 6488.TPEX 在 2026-09-17 的 prev_close=914.0（9/15 收盤）、change_pct=-0.00219；官方 reference csv\tpex_trading_stock_202608_202609.csv：6488 在 9/16 收盤 937、9/17 收盤 912、漲跌 -25（約 -2.67%）。8069.TPEX 同樣以 9/15 的 146.0 作為前收
      - 官方參考資料 csv\tpex_trading_stock_202608_202609.csv 與 csv\index_daily_202608_202609.csv：TPEx 在 2026-09-16、09-23 有成交（6488、8069 的日K；櫃買指數 9/16 收 399.21、9/23 收 413.76）
      - 線上來源 https://en.wikipedia.org/wiki/Taipei_Exchange：『a non-profit foundation set up to manage over-the-counter (OTC) market and bond trading in Taiwan』；『normal trading sessions from 9 am to 2 pm ... on all days of the week except Saturdays, Sundays and holidays』
      - 線上來源 https://zh.wikipedia.org/wiki/證券櫃檯買賣中心：『截至2026年6月，上櫃公司約有891檔』；https://zh.wikipedia.org/wiki/臺灣證券交易所：『截至2026年8月，證交所共有1,085家上市公司』，上櫃約占上市加上櫃總數的 45%
      - 線上來源 https://www.python-httpx.org/exceptions/：RemoteProtocolError 屬於 TransportError → ProtocolError，定義為『The protocol was violated by the server』，是傳輸層的錯誤；同晚 21:40 對 tpex.org.tw 的 share capital 請求成功（backend.log:45475 起），可推知重抓就可能成功（推測）
      - CONTRIBUTING.md:285『網絡失敗、無權限、空數據、能力缺失和部分寫入時不得產生錯誤狀態』
      - docs/radar-strategies-validation.md:128-130『不會用殘缺資料計算』只考慮到缺整個日期的情形，沒有涵蓋缺一個市場的分區；docs\code-review-2026-09-22.md 裡沒有此問題的記錄（grep snapshot_provider、fetch_date、TPEx snapshot 都沒有命中）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：snapshot_provider.fetch_date 在 TPEx 抓取失敗時只記 warning，回傳只有上市資料的 frame。refresh_dates 照樣寫入分區，而且不會把這個日期列入 failed_dates，所以 run_update 判為 daily=success，daily_pipeline 只擋 'failed' 的閘門也擋不到。

21:40 那班在 daily_update.py:282-286 看到 d_max ≥ target 就判為「已是最新」，連 refresh_dates 都沒呼叫。latest_date() 也把這個殘缺分區當成完整的基準日。

結果：2026-09-16 與 09-23 的 taiwan/daily 與 enriched 都只有上市約 1320～1330 列，上櫃 0 列；官方資料顯示兩天上櫃都有交易，9/23 經 allowlist 過濾後應有 1007 列。9/23 的策略快取裡，20 支策略全部 0 檔上櫃，其中 15 支有命中，552 檔全是上市。對照 9/22：finding 引用的 4 支趨勢類策略上櫃占 29%（111/382），20 支合計占 24%（188/779）。

9/16 的缺口至今沒有回補，會讓後續上櫃股的逐列指標算錯。例如 9/17 的 6488.TPEX，prev_close 取成 9/15 的 914 而不是 9/16 的 937，漲跌幅 -0.22% 應為 -2.67%。9/16 當天的失敗原因因 log 已輪替而無法確認。
    - 理由：我自己重算了一遍，finding 的核心機制和所有數字都重現了。有兩處需要修正：影響範圍比 finding 寫的大；21:40 那班實際跳過的位置也跟 finding 說的不同。另外找到一個新面向。

1) 本地 parquet 列數（count_exchange.py）：taiwan/daily 在 2026-09-16、09-23 都只有 1330 列，全部是 TWSE，TPEX 為 0。前後幾天（9/15、9/17、9/22）TPEX 分別是 1006、1006、1009。kline_daily_enriched 在 9/16、9/23 的 TPEX 也是 0（TWSE 為 1319、1323），其他日約 975～993。9/16 分區的 mtime 是 9/16 16:30:22，之後從沒被重寫；9/23 分區是 16:30:05 寫入的。

2) 官方參考資料證明兩天上櫃都有交易，不是休市：TPEx 指數 9/16 收 399.21、9/23 收 413.76；6488 在 9/16 成交 6,666 張、9/23 成交 28,715 張。9/23 的 dailyQuotes 原始回應餵進 app 自己的 _parse_tpex_table，經 allowlist 過濾後有 1007 列。本地 9/16、9/23 分區裡 6488.TPEX、8069.TPEX 都不存在；9/15、9/17、9/22 的 OHLC 與官方一致。

3) 離線模擬失敗路徑（simulate_failure.py，用 MockTransport，不連網）：
- TWSE 回官方 9/23 原始資料、TPEx 拋 RemoteProtocolError 時，fetch_date 回傳 1330 列、TPEX 0，數量與本地分區完全一致。
- refresh_dates 照樣寫入分區，stats 為 dates_fetched=1、failed_dates=[]，所以上層判為 daily=success。
- 第二輪 TPEx 已恢復，但被 dates_skipped=1 跳過，HTTP 呼叫數 0。
- 只有 force=True 才會重抓，結果 2337 列、TPEX 1007。

4) log：backend.log 第 37258 行記了 2026-09-23 16:30:05 的 TPEx 失敗警告。第 37265～37266 行顯示 daily=success、overall=success，第 37359 行寫入策略快取，552 命中。21:40 那班（第 45470～45478 行）完全沒有發出 MI_INDEX 或 dailyQuotes 請求。9/16 沒有 log 可查：backend.log.3 從 9/17 11:14 開始，backend.log.2 不存在，所以 9/16 的失敗原因確實無法確認。

5) 策略結果：我照 compute_and_cache_all 寫了一份不寫快取的重算（rerun_strategies.py）。9/23 重算的 20 支策略命中數與 strategy_cache.json 完全相同，總計 552，全部是 TWSE、TPEX 0，證明重算方式可靠。9/22 重算得 bullish_alignment 69/237、trend_breakout 8/25、macd_golden 9/35、boll_breakout 25/85，和 finding 的數字一字不差。

修正與補充：
(a) 影響不只 7 支策略。全部 20 支策略都是 0 檔上櫃，其中 15 支有命中，552 檔全是上市。
(b) 9/22 的上櫃占比：finding 引用的 4 支策略是 111/382＝29%，20 支策略合計是 188/779＝24%。「約三成」只對趨勢類成立。
(c) 21:40 那班實際在 daily_update.py:282-286 就停下了（d_max ≥ target → "Daily store already current"），根本沒呼叫 refresh_dates。daily_refresh.py:167 是另一層同樣會跳過的機制。另外 refresh_dates 對部分失敗回報 failed_dates=[]，所以 daily_pipeline.py:169 只擋 'failed' 的閘門也擋不到。
(d) 新面向：9/16 的缺口永久留在歷史裡。指標用逐列的 rolling／shift 計算（pipeline.py:393-399 用 .over('symbol')），所以之後的上櫃指標會算錯。例如 9/17 enriched 的 6488.TPEX：prev_close=914（這是 9/15 收盤），change_pct 算成 -0.22%；官方前收應為 9/16 的 937，漲跌 -25，即 -2.67%。
(e) docs/radar-strategies-validation.md:128-130 說 latest_date() 只會退回完整落盤日。但 repository.py:1426 的 taiwan_official_latest_date 只看分區是否存在，只有上市資料的分區也算數，所以這句敘述不成立。

這個問題不在 code-review-2026-09-22.md 已記錄的清單裡。嚴重度維持 P1：整個上櫃市場無聲消失，是錯誤的金融口徑，且影響核心的選股流程；資料可以用 force 回補，不算資料破壞。唯讀驗證：兩次執行後用 find -newer 比對，data 與 backend/app 都沒有新寫入的檔案（backend.log 是執行中的後端 watchfiles 寫的）。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-118：TPEx 例外只記 logger.warning；:120-124 只要有任一個 frame 就回傳
      - backend/app/taiwan/daily_refresh.py:167-176：日期在 available 裡就跳過；df 非空就 write_batch，且不會記入 failed_dates
      - backend/app/taiwan/daily_update.py:282-286：d_max ≥ target 時標成 'Daily store already current'，不會呼叫 refresh_dates（21:40 那班實際在這裡停下）；:308-311 failed_dates 為空就判 success
      - backend/app/jobs/daily_pipeline.py:169 只擋 daily.status=='failed'；:193-195 註解宣稱 21:40 是第二次機會
      - backend/app/repository.py:1426：taiwan_official_latest_date 取 available_dates()[-1]，只有上市資料的分區也算數
      - count_exchange.py 輸出（taiwan/daily）：9/15 TWSE 1330／TPEX 1006；9/16 TWSE 1330／TPEX 0（mtime 09-16 16:30:22）；9/17 1328／1006；9/22 1331／1009；9/23 1330／0（mtime 09-23 16:30:05）
      - count_exchange.py 輸出（kline_daily_enriched）：9/16 TWSE 1319／TPEX 0；9/22 1328／988；9/23 1323／0；9/17～9/23 合計 10576 列，與 log 的 'taiwan enriched refresh: 10576 rows' 一致
      - data/backend.log:37258 '2026-09-23 16:30:05 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)'；:37265 'daily=success'；:37359 '策略缓存已写入: 2026-09-23, 20 策略, 552 命中'
      - data/backend.log:45470-45478：21:40 那班沒有 MI_INDEX／dailyQuotes 請求，daily=success，之後又寫入 552 命中
      - 官方 TPEx dailyQuotes 9/23（https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?response=json&date=2026/09/23，參考資料 raw/tpex_daily_quotes_20260923.json）：經 app 的 _parse_tpex_table＋allowlist 過濾後有 1007 列
      - 官方 TPEx 指數（https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?response=json&date=2026/09/01）：TPEX_INDEX 2026-09-16 收 399.21、2026-09-23 收 413.76；tradingStock 6488：9/16 成交 6666 張、收 937，9/23 成交 28715 張、收 950
      - simulate_failure.py 輸出：round1 fetch_date 1330 列、TPEX 0；refresh_dates stats {dates_fetched:1, failed_dates:[]}；round2（TPEx 已恢復）{dates_skipped:1}，新 HTTP 呼叫 0；resolve_missing_date_range(9/23,9/23)=None；force=True 時 2337 列、TPEX 1007
      - strategy_cache.json（as_of 2026-09-23）：20 支策略全部 TPEX=0，命中總數 552 全是 TWSE（boll_breakout 48、bullish_alignment 166、low_volatility_leader 135、trend_breakout 12、macd_golden 10…）
      - rerun_strategies.py 輸出：9/23 重算與快取逐支一致（總計 552，TPEX 0）；9/22 為 bullish_alignment 237 中 TPEX 69、trend_breakout 25 中 8、macd_golden 35 中 9、boll_breakout 85 中 25；20 支合計 779 中 TPEX 188（24.1%）
      - kline_daily_enriched date=2026-09-17 的 6488.TPEX：close 912、prev_close 914（9/15 收盤）、change_pct -0.0022；官方 9/16 收 937、9/17 漲跌 -25（-2.67%）
      - backend.log.3 從 2026-09-17 11:14 開始，backend.log.2 不存在，9/16 的失敗原因沒有 log 可查
      - 腳本與輸出位置：AUDIT_SCRATCH/work/verify-tpexgap/（count_exchange.py、simulate_failure.py、rerun_strategies.py、rerun_2026-09-22.json、rerun_2026-09-23.json）

### [P3 → P3] bullish_alignment 的進場命中被標示為『MA5上穿MA20（金叉）』，但條件其實是持續狀態（確認成立）

- **主張**：entry_signal_code 一律是 0，對應 entry_signal_ids[0]='signal_ma_golden_5_20'；bullish_alignment 的進場條件是均線排列狀態，並非金叉事件。monitor 會用這個名稱發出 buy_signal。
- **正確的定義或數值**：命中名稱應為『均線多頭排列』之類的狀態訊號 id。
- **app 實際行為**：監控與回測記錄會把每天的多頭狀態記成金叉。
- **對使用者的影響**：目前監控規則為 0 條，只影響之後啟用策略監控時的告警文字。
- **修法建議**：新增 signal_bullish_alignment 作為 entry_signal_ids 的唯一項目。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/bullish_alignment.py:43
  - backend/app/strategy/builtin/bullish_alignment.py:74-77
  - backend/app/strategy/engine.py:1384-1395
  - backend/app/strategy/monitor.py:1147-1155
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 共 166 檔命中，enriched 的 signal_ma_golden_5_20 欄位在大多數列為 False（快取列可以查到），名稱與事實不符
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：bullish_alignment 的進場條件是持續狀態，但每筆進場命中的 entry_signal_code 都是 0，所以一律被標成 signal_ma_golden_5_20，中文標籤是「MA5上穿MA20」，不是「MA5上穿MA20（金叉）」；這個 id 在 app 裡是當日穿越事件。9/23 快取的 166 檔命中中，165 檔當天沒有金叉；自組 61 根日K 用 app 純計算重跑得到 227 檔，其中只有 2 檔真的金叉，全部都帶這個標籤。

影響有三處：
- 監控：策略規則若自行開啟 buy_signal（預設只通知 pool_entry/pool_exit），每天換日時所有仍在多頭狀態的標的都會再觸發一次買入訊號，並附「MA5上穿MA20」標籤；超過 5 檔會合併成批次事件。
- 回測：交易記錄的 entry_signal_id 也寫成 signal_ma_golden_5_20，但前端目前沒有顯示這個欄位。
- 其他：entry_signal_ids 的第二項 signal_ma_golden_20_60 永遠用不到。pullback_ma20_bounce 和 pullback_to_support 也有同樣的錯標，它們的進場條件同樣不是金叉事件。

使用者目前沒有監控規則，所以這個問題不影響選股結果與交易結果。
    - 理由：這條 finding 的核心說法成立，數值也能重現。bullish_alignment 的進場條件是「持續狀態」：MA5>MA10>MA20>MA60，而且 20 日動量大於 0。但 entry_signal_code 在每個命中位置都是 0，所以每筆命中都被標成 signal_ma_golden_5_20。這個 id 在 app 裡是「事件」訊號，意思是前一日 MA5<=MA20、當日 MA5>MA20。線上定義也把交叉定為穿越那一刻的事件。

數據重現分三組，全部顯示命中幾乎都不是當天金叉：
(1) strategy_cache.json 在 9/23 的 bullish_alignment 共 166 列。其中 signal_ma_golden_5_20 為 False 的有 165 列，True 只有 1 列（00935.TWSE）。166 列也都符合 ma5>ma10>ma20>ma60 且 momentum_20d>0。finding 的「166 檔、大多數為 False」完全重現。
(2) 我用 enriched 最近 61 根日K 自組面板，呼叫 app 的純計算 MatrixStrategyPipeline 與 StrategyEngine._matrix_signal_hits，得到 227 檔命中。entry_signal_code 只有 {0} 一種，227 檔全部帶 ['signal_ma_golden_5_20']，但 enriched 欄位只有 2 檔當天真的金叉。
(3) 用 polars 獨立重算有 228 檔，其中真金叉 2 檔；直接用 enriched 內存欄位篩選有 232 檔，其中金叉 2 檔。
快取的 166 與重算的 227–232 有落差，原因未查（快取產生於 9/23 21:40，可能和當時的資料狀態或宇宙有關）。這個落差不影響結論。

有幾處細節需要修正：
- 前端與後端告警顯示的中文是「MA5上穿MA20」（monitor.py:39、signals.ts:28），沒有「（金叉）」。那個字樣只出現在 pipeline.py:177。
- buy_signal 通知要使用者自行開啟。後端與前端的預設通知事件都只有 pool_entry 和 pool_exit（monitor.py:1138、strategyMonitorEvents.ts:43-46）。開啟後，換日時所有仍在狀態中的標的都會再觸發一次 buy_signal（monitor.py:1235）；超過 5 檔會合併成 1 則批次事件，帶一個「MA5上穿MA20」標籤（monitor.py:1185-1199）。
- 回測交易記錄的 entry_signal_id 會寫入 signal_ma_golden_5_20（backtest/engine.py:1097-1099、backtest/strategy.py:2075），但前端只在 api.ts:1860 宣告型別，沒有顯示。
- entry_signal_ids 的第二項 signal_ma_golden_20_60 是死碼，因為進場代碼永遠不會是 1。

原記錄沒提到的新面向：pullback_ma20_bounce 和 pullback_to_support 也有同樣的錯標。它們的進場條件同樣不是金叉事件，卻都以 signal_ma_golden_5_20 當唯一的 entry id。9/23 快取裡分別有 13/17 和 41/43 列不是金叉。

嚴重度維持 P3。使用者目前沒有任何監控規則，buy_signal 也要自行開啟，影響只限於日後的告警標籤與回測 API 的欄位語意，不改變選股結果或交易結果。
    - 證據：
      - backend/app/strategy/builtin/bullish_alignment.py:63-66 進場條件＝(ma5>ma10)&(ma10>ma20)&(ma20>ma60)&(momentum_20d>0)，是持續狀態
      - backend/app/strategy/builtin/bullish_alignment.py:74,76 entry_signal_code=np.where(entry, 0, -1)；entry_signal_ids=("signal_ma_golden_5_20", "signal_ma_golden_20_60")，第二項永遠用不到
      - backend/app/indicators/pipeline.py:622-624 signal_ma_golden_5_20 的定義是事件：(ma5>ma20) & (前一日 ma5<=ma20)
      - backend/app/strategy/engine.py:1223-1228,1384-1395 matrix 路徑把代碼 0 對應成 signals=['signal_ma_golden_5_20']
      - backend/app/strategy/monitor.py:1147-1174,936 entry_signal_hits 的 signals 寫進告警事件的 ev['signals']；前端 AlertToast.tsx:203、Dashboard.tsx:155 以 cnSignal 顯示；monitor.py:39、frontend/src/lib/signals.ts:28 的標籤是『MA5上穿MA20』
      - backend/app/strategy/monitor.py:1138 預設 notify_events=('pool_entry','pool_exit')；frontend/src/lib/strategyMonitorEvents.ts:43-46 前端預設也只有這兩項，buy_signal 要自行開啟
      - backend/app/strategy/monitor.py:1235 換日時 candidates=current，狀態型命中每天重新觸發 buy_signal；1185-1199 超過 5 檔合併成批次事件
      - backend/app/backtest/engine.py:1097-1099 交易記錄的 entry_signal_id 由代碼 0 對應成 signal_ma_golden_5_20；backend/app/backtest/strategy.py:2075 輸出到 API；前端只在 frontend/src/lib/api.ts:1860 宣告型別，沒有顯示
      - 本地快取（腳本 AUDIT_SCRATCH/work/verify-bagold/cache_check.py）：cache bullish_alignment total: 166；signal_ma_golden_5_20: {False: 165, True: 1}（True 為 00935.TWSE）；166 列都符合 ma5>ma10>ma20>ma60 & mom20>0，違反 0 列
      - 本地重算（腳本 AUDIT_SCRATCH/work/verify-bagold/repro.py 61，視窗 2026-06-30→2026-09-23）：C: app matrix entry hits: 227；distinct entry_signal_code: [0]；signal labels: {('signal_ma_golden_5_20',): 227}；enriched 欄位中 True 2 / False 225。A: polars 重算 228，其中真金叉 2。B: enriched 內存欄位 232，golden_5_20 True 2 / False 230
      - 快取 166 與重算 227–232 的落差（快取 enriched_mtime 1790170809，約 9/23 21:40）原因未查（推測與快取產生時的資料狀態或宇宙有關），不影響結論
      - 新面向：backend/app/strategy/builtin/pullback_ma20_bounce.py:66-73,83,85 與 backend/app/strategy/builtin/pullback_to_support.py:78-91,98,100 的進場條件都不是金叉事件，卻以 signal_ma_golden_5_20 為唯一 entry id；9/23 快取 pullback_ma20_bounce {True: 4, False: 13}、pullback_to_support {True: 2, False: 41}；對照組 ma_golden_cross {True: 11}
      - 線上定義 https://en.wikipedia.org/wiki/Moving_average_crossover："A crossover occurs when a faster moving average (i.e., a shorter period moving average) crosses a slower moving average"；"Golden cross occurs when 50 days simple moving average crosses 200 days simple moving average from below."，說明交叉指的是穿越事件
      - 唯讀檢查：執行前後 git status --porcelain 相同（diff 無輸出）

### [P2 → P1] 台股全程使用未還原價：除權息缺口在趨勢策略中大量產生假的出場與動能訊號（部分成立）

- **主張**：台股 enriched 刻意不做還原（factors 為空），close==raw_close。7 支策略的 MA、布林、動能、60 日極值都直接用未還原價，除息、除權、減資或分割當日的價格缺口會被當成真實下跌。
- **正確的定義或數值**：趨勢類技術指標與回測報酬應使用還原（前復權）價；漲跌停判斷才用原始價（CONTRIBUTING.md:72、101-102 的原則）。
- **app 實際行為**：指標用原始價，除息日被當成下跌，高息 ETF 長期被排除在多頭排列之外，持有中的部位則被假出場訊號洗出場。
- **對使用者的影響**：使用者自選的 5 檔 ETF（00878、0050、00885、00893 等）每次配息都會出現假的跌破訊號，而且 20～60 天內無法入選多頭排列；回測把配息當成虧損（回測部分屬推測，未追蹤回測引擎）。
- **修法建議**：以 TWT49U 的 ex_ref_price/pre_ex_close 建立台股還原因子（adj_factor），讓 enriched 的 OHLC 做前復權，並保留 raw_*；或至少讓策略矩陣改用還原序列。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95（compute_enriched(raw, factors=pl.DataFrame(), ...)）
  - backend/app/taiwan/providers/hybrid_provider.py:31（adj_factor=False）
  - backend/app/indicators/pipeline.py:1023（只有 factors 非空時才還原）
  - backend/app/strategy/builtin/bullish_alignment.py:67-69（出場：ma_dead、ma20_breakdown）
  - backend/app/strategy/builtin/boll_breakout.py:61（出場：close<lower）
- **線上證據**：
  - Yahoo chart API 00878.TW：2026-08-17 close 33.75、adjclose 32.74（比率 0.97007），第三方有提供還原序列；https://query1.finance.yahoo.com/v8/finance/chart/00878.TW?range=1y&interval=1d&events=div%2Csplit
- **本地計算證據**：
  - adj_check.py：enriched 共 1,423,597 列，close≠raw_close 的列數為 0；data/adj_factor、adj_factor_etf 為空
  - exdiv.py（官方 TWT49U，2026-06-01～09-23，比對到 956 次，中位跌幅 2.82%）：只用未還原價才出現的訊號有 MA20 跌破交叉 221 次、MA5/20 死叉 62 次、收盤低於 MA20 274 次、跌破布林下軌 219 次、20 日動能為負 225 次
  - 自選 00878 在 8/18 除息 1.01 元：未還原價觸發 MA20 跌破交叉（bullish/trend 的出場訊號），還原後不觸發
  - adj_picks.py：若以還原價計算，9/23 bullish_alignment 會多 18 檔（00919、00918、00923 等高息 ETF），ma_golden 多 2、swing 多 2、macd 多 2 少 1
  - 6669.TWSE 在 9/2 除權（TWT49U 跌幅 66.5%），收盤 7800→2610，此後 60 天的 MA60、low_60d、momentum_60d 全部失真
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 的 OHLC 完全未還原：provider 的 adj_factor=False，enriched_refresh 傳入空的 factors，實測 close 與 raw_close 在全部列都相同。這違反 CONTRIBUTING.md:100「enriched OHLC 為前復權價格」的契約，屬於錯誤的金融口徑，應判 P1。

後果分兩層：
1. 趨勢與突破類策略的 MA、布林、動能、60 日極值會把除權息缺口當成真實下跌。官方 956 次除權息事件中，有 222 次只在未還原價下出現 MA20 跌破交叉，只在還原價下出現的為 0 次。
2. 回測以未還原價計算 pnl 與停損（engine.py:751、970），而且完全沒有股利處理。因此配息被記為虧損；股票分割或大比例配股則會產生巨額假虧損，例如 0050 在 2025-06-18 的 1 拆 4 為 −74.8%、6669 在 2026-09-02 為 −66.5%。

在使用者情境下的影響：自選 00878 於 8/18 除息當天出現假的 MA20 跌破，8/21 出現假死叉，被排除在多頭排列之外的時間比還原後多 7 個交易日。自選中其餘有除權息的標的，除息當天沒有出現只在未還原價下的跌破。原 finding 說「5 檔 ETF 每次配息都會出現假訊號」「20～60 天無法入選」屬於誇大；60 天失真只在分割或大比例配股時成立。
    - 理由：核心主張成立。台股 enriched 刻意不做還原：provider 宣告 adj_factor=False，get_adj_factors 固定回傳空表，enriched_refresh 呼叫 compute_enriched 時傳入空的 factors。compute_enriched 只有在 factors 非空時才做前復權，所以 close 就等於 raw_close。實測全部 1,423,597 列中，close≠raw_close 的列數為 0。

這違反專案自己的資料契約：CONTRIBUTING.md:100 明定「enriched 的 open/high/low/close 為前復權價格」。線上權威定義也指向同一方向：
- Yahoo 說明頁：還原收盤價是「依 CRSP 標準，對分割與股利做調整」後的價格。
- QuantConnect LEAN 回測引擎：預設對分割與股利做調整，以產生平滑的價格曲線。
- Wikipedia：除權息參考價 =（前一日收盤價 − 現金股利）÷（1＋配股率）。00878 代入後得 (33.75−1.01)/1 = 32.74，與官方 TWT49U 的 ex_ref_price 32.74 完全一致。

因此除息日的價格缺口是參考價的機械性重設，並不是市場真的下跌；未還原的 MA、動能、極值都會把它當成下跌。

我獨立重算的結果：
- 956 次官方除權息事件中，只在未還原價下出現的 MA20 跌破交叉有 222 次（finding 為 221 次），只在還原價下出現的為 0 次。
- 00878 在 8/18：未還原收盤 32.38 低於 MA20 32.716，觸發跌破；還原後 MA20 為 31.785，不觸發。8/21 另有一次只在未還原價出現的 MA5/20 死叉，8/27 又一次假跌破。

finding 自己標為推測的「回測把配息當虧損」，我已從程式碼確認：
- 回測以 enriched 的 close/open 計算 pnl（engine.py:751）。
- 開盤跳空低於停損線時，以開盤價出場（engine.py:970）。
- app/backtest 全目錄沒有任何股利處理。

finding 還漏掉一個更嚴重的面向：股票分割與大比例配股同樣未還原。0050 在 2025-06-18 做 1 拆 4，收盤從 188.65 變 47.57（−74.8%），當天 MA60 仍是 171.95。6669 在 9/2 從 7800 變 2610（−66.5%）。在漲跌幅限制 ±10% 的市場裡，資料內仍有 151 個單日跌幅超過 15% 的交易日，這些都是公司行動造成的缺口。凡是跨越這類日期的回測部位，都會以約 −75% 的假虧損被停損出場。

依 CONTRIBUTING 第 11 節，「錯誤的金融口徑」屬於 P1，所以原判 P2 偏低。

使用者影響部分有誇大：
- 自選清單裡只有 4 檔 ETF（0050、00878、00885、00893），不是 5 檔。
- 6/1～9/23 期間，00885、00893 在 TWT49U 裡沒有除息紀錄。
- 0050 在 7/21 除息當天，未還原與還原都沒有觸發 MA20 跌破（收盤 102.5，兩種 MA20 分別為 105.88 與 105.27，兩者都已在均線下方）。
- 期間內有除權息事件的 6 檔自選，除息當天只有 00878 出現只在未還原價下的跌破。
- 00878 因未還原而被排除在多頭排列之外的天數：未還原為 8/20～9/3 共 11 個交易日，還原後為 8/21～8/26 共 4 天，淨多 7 個交易日，不是「20～60 天」。
- 「60 天失真」只在 6669、0050 分割這類大缺口時成立。

綜合判定：部分確認，嚴重度應上修為 P1。
    - 證據：
      - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:151 get_adj_factors 的 docstring「Return empty adjustment factors (Phase 2 records raw price semantics)」，並 return pl.DataFrame()
      - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/indicators/pipeline.py:1023-1024 只有 factors 非空時才呼叫 _apply_adj_factor；:1016-1020 raw_close 從 close 複製而來
      - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」；:102 漲跌停才用原始價。專案契約與台股實作相反
      - backend/app/strategy/builtin/bullish_alignment.py:65-66 出場條件 ma_dead、ma20_breakdown 使用 market.close；boll_breakout.py:61 exit_ = market.close < lower
      - backend/app/backtest/engine.py:751 pnl = (float(sym_close[i]) - entry_price) / entry_price；:970 開盤價低於停損線時以 open_price 出場；grep app/backtest 沒有任何 dividend／除權息處理
      - 本地（v1.py）：enriched 共 1,423,597 列，close≠raw_close 為 0 列；data/adj_factor 與 data/adj_factor_etf 為空目錄
      - 本地（v2.py）：00878.TWSE 在 2026-08-18 收盤 32.38，未還原 MA20 32.716（brk=true），以官方因子 32.74/33.75 還原後 MA20 31.785（brk=false）；8/21 只在未還原出現死叉；多頭排列未還原為 false 的期間是 8/20～9/3（11 日），還原為 false 的期間是 8/21～8/26（4 日）
      - 本地（v4.py，官方 TWT49U 994 筆、對到 enriched 956 筆）：除息當天只在未還原出現的 MA20 跌破有 222 次，只在還原出現的有 0 次；自選 6 檔有事件的標的中，只有 00878 當天觸發
      - 本地（v3.py）：0050.TWSE 在 2025-06-18 收盤從 188.65 變 47.57（−74.78%），MA60 仍為 171.95；6669.TWSE 在 2026-09-02 從 7800 變 2610（−66.54%）；全資料單日跌幅超過 15% 的有 151 筆
      - 官方參考 twse_twt49u_20260601_20260923.csv：00878 在 2026-08-18 除息，pre_ex_close 33.75、ex_ref_price 32.74、股利 1.01；0050 在 2026-07-21 除息 0.6；00885、00893 在此期間無紀錄
      - https://help.yahoo.com/kb/SLN28256.html：「Adjusted close is the closing price after adjustments for all applicable splits and dividend distributions.」「Data is adjusted using appropriate split and dividend multipliers, adhering to Center for Research in Security Prices (CRSP) standards.」
      - https://www.quantconnect.com/docs/v2/writing-algorithms/securities/asset-classes/us-equity/requesting-data：「By default, LEAN adjusts US Equity data for splits and dividends to produce a smooth price curve.」
      - https://en.wikipedia.org/wiki/Ex-dividend_date：「The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend」；https://zh.wikipedia.org/wiki/除權：除權參考價「(除息前一日收盤價－現金股利)÷(1＋配股率)」
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 沒有做前復權：enriched_refresh.py:95 傳入空的 factors，provider 的 adj_factor=False，data/adj_factor 也是空的，所以 close 等於 raw_close（1,423,597 列全部相同）。這違反 CONTRIBUTING.md:100「enriched OHLC 為前復權」的契約。所有策略的 MA、布林、動能、60 日極值，以及回測（engine.py:509 讀同一份 close，app/backtest 沒有任何股利處理），都把除權息缺口當成真實下跌。

用官方 TWT49U（2026-06-01～09-23）重算：比對到 956 次事件，中位跌幅 2.82%。除權息當日只出現在未還原價的訊號約為：MA20 跌破 222、MA5/20 死叉 63、收盤低於 MA20 275、跌破布林下軌 219、20 日動能為負 225。9/23 若改用還原價，bullish_alignment 會多 18 檔（10 檔 ETF，其中高息型只有 00918、00919、00961、00964；另有 8 檔普通股），ma_golden_cross 與 swing_trend_confirmation 各多 2 檔，macd_golden 多 2 少 1。6669 在 9/02 除權（跌 66.5%）後，MA60、low_60d、momentum_60d 嚴重失真（9/23 的 momentum_60d：未還原 −54.6%，還原 +35.5%）。

對使用者自選股的實際影響：窗口內只有 00878（8/18）產生明顯的假訊號，包括 8/18 的 MA20 跌破、8/21 的死叉，以及 8 個交易日被排除在多頭排列之外。0050 在 7/21 除息只有 0.6%，沒有產生假跌破訊號。00885、00893 在窗口內沒有除息。自選普通股 3034、3545、2379、6196 也出現假的跌破或死叉，或者訊號日期偏移。監控規則為 0 條，所以受影響的是選股、訊號顯示與回測，不會觸發即時告警。
    - 理由：核心機制與所有本地數值都重現了。我沒有沿用原稽核的重算方法，改用 app 自己的程式碼：讀 data/taiwan/daily 原始日 K（1,435,948 列），呼叫 compute_enriched 兩次。第一次 factors 為空，也就是目前部署的算法，重算結果與已存的 kline_daily_enriched 逐列完全相同（1,423,597 列；close、ma20、ma60、boll_lower、momentum_20d、low_60d 的最大差都是 0.0；三個訊號欄都是 0 筆不符）。第二次用官方 TWT49U 算出 ex_factor = pre_ex_close / ex_ref_price，交給 app 的 _apply_adj_factor 做前復權。兩次結果並列比較：比對到 956 次事件、中位跌幅 2.82%，與 finding 相同；只出現在未還原價的訊號是 MA20 跌破 222（finding 221）、MA5/20 死叉 63（62）、收盤低於 MA20 275（274）、跌破布林下軌 219（219）、20 日動能為負 225（225）。差 1 的原因是方法不同：原稽核只讀 2026 年的分區、每次事件只乘單一因子；我用全歷史加累積因子。9/23 選股用 app 內建的 compute_signals，並套用引擎預設的 price_min 3、amount_min 2e7，結果與 finding 完全一致：bullish_alignment 166 檔變 184 檔（多 18 檔、少 0 檔），ma_golden_cross 多 2，swing_trend_confirmation 多 2，macd_golden 多 2 少 1；trend_breakout、consolidation_breakout、boll_breakout 都沒有差異。00878 在 8/18 的 MA20 跌破只出現在未還原價，也重現了。Yahoo 線上數值（8/17 close 33.75、adjclose 32.74、比率 0.97007）與我用 app 算出的還原價 32.74 相同。6669 在 9/2 除權（跌 66.5%，收盤 7800→2610）後，MA60、low_60d、momentum_60d 都失真，也重現了。回測部分我補查過：回測讀的是同一份 kline_daily_enriched 的 close，app/backtest 裡完全沒有股利或除權處理，所以「回測把配息當成虧損」有程式碼證據，但我沒有實際跑回測。

判部分成立，是因為 user_impact 與 actual 有幾處事實錯誤或誇大。(1) 自選 9 檔中 ETF 只有 4 檔（0050、00878、00885、00893），不是 5 檔。(2) 00885、00893 在 2026-06-01～09-23 沒有任何 TWT49U 除息紀錄。0050 在 7/21 除息（0.6%）時沒有產生只出現在未還原價的跌破訊號；相反地，8/03 還原價有 MA20 跌破，未還原價反而漏掉。所以「每次配息都會出現假跌破」不成立。(3) 「20～60 天內無法入選多頭排列」誇大：實測 00878 因未還原而被排除在多頭排列之外的只有 8 個交易日（8/19、8/20、8/27～9/03），0050 只有 1 天（8/27）。(4) 9/23 多出的 18 檔中，ETF 10 檔、普通股 8 檔；ETF 裡真正是高息型的只有 00918、00919、00961、00964。finding 點名的 00923 是群益台ESG低碳50，不是高息 ETF。(5) 自選的普通股也受影響，finding 沒提：3034 在 7/13～7/14 出現假的布林下軌跌破，多頭排列少了 7 天；3545 在 8/14 出現假的 MA20 跌破；2379、6196、3034 的死叉或跌破日期被提前或推後。(6) 「刻意不做還原」找不到文件或 commit 說明（fc7e40a 的訊息沒提到），只能說是「沒有做」。「減資或分割」只是機制上成立，TWT49U 不含減資，我沒有用資料重現。(7) 使用者的監控規則是 0 條，所以假出場訊號不會觸發即時告警。實際受影響的是選股結果、策略訊號顯示與回測；「持有部位被洗出場」目前只會發生在回測。

嚴重度：CONTRIBUTING.md:100 明文規定 enriched 的 OHLC 是前復權價；台股實作違反這條契約，而且回測直接用這個價格、沒有股利補償，符合第 11 節表格中 P1 的「錯誤金融口徑」。所以我建議從 P2 上調為 P1。台股散戶的技術分析常以未還原價為預設，若認為選股差異只是中等（例如 bullish 184 檔中差 18 檔），維持 P2 也說得通。
    - 證據：
      - 程式碼：backend/app/taiwan/enriched_refresh.py:95 compute_enriched(raw, factors=pl.DataFrame(), ...)；backend/app/indicators/pipeline.py:1023-1024 只有 factors 非空時才呼叫 _apply_adj_factor；backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；data\adj_factor、adj_factor_etf 用 find 查無任何檔案
      - 契約：CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」（finding 引用 101-102，最直接的是第 100 行）；CONTRIBUTING.md:295 P1 包含「錯誤金融口徑」
      - 回測：backend/app/backtest/engine.py:509 parquet_root = data_dir / enriched_dirname(asset_type)；backend/app/repository.py:41-43 stock 對應 kline_daily_enriched；在 app/backtest 下 grep -i 'dividend|ex_rights|除息|除权' 沒有任何命中
      - 重算基準：s1_load.py 輸出 stored enriched rows 1423597、close!=raw_close 0、high!=raw_high 0、low!=raw_low 0；s2_events.py：以 compute_enriched(factors 為空) 重算，與已存 enriched 的 close/ma20/ma60/boll_lower/momentum_20d/low_60d 最大差都是 0.0，三個訊號欄都是 0 筆不符
      - 官方對照：TWSE STOCK_DAY 參考檔 380/380 列收盤與本地日 K 相同（本地是未還原官方價）；956 次事件的 pre_ex_close 都等於本地前一日收盤
      - 除權息日訊號（我的數字／finding 的數字）：比對到的事件 956/956；中位跌幅 2.82%/2.82%；MA20 跌破 222/221；MA5/20 死叉 63/62；收盤低於 MA20 275/274；跌破布林下軌 219/219；20 日動能為負 225/225。差 1 來自方法不同：原稽核只讀 2026 年分區且只乘單一事件因子，我用全歷史加 app 的累積因子
      - 9/23 選股（s3_picks.py，app 內建的 MATRIX_STRATEGY.compute_signals，未還原 vs 還原）：bullish_alignment 166 檔 vs 184 檔（還原才入選的 18 檔：00404A、00770、00881、00896、00918、00919、00923、00961、00964、009802、2347、2428、2527、2838、3031、4763、9917、9933）；ma_golden_cross 11 vs 13；macd_golden 10 vs 11（還原多 3033、5258，少 1102）；swing_trend_confirmation 6 vs 8；trend_breakout、consolidation_breakout、boll_breakout 都沒有差異。全歷史與 122 日窗口的結果相同
      - 00878（s2_events.out）：8/17 收盤 33.75；8/18 收盤 32.38，未還原 MA20 32.7155，signal_ma20_breakdown 為 true；還原後 MA20 31.7849，訊號為 false。另外 8/21 的死叉、8/27 的 MA20 跌破也都只出現在未還原價
      - 線上：https://query1.finance.yahoo.com/v8/finance/chart/00878.TW?range=3mo&interval=1d&events=div%2Csplit 回傳 HTTP 200：2026-08-17 close 33.75、adjclose 32.74、比率 0.97007；股利事件 amount 1.01。app 用 TWT49U 因子算出的 8/17 還原價是 32.74，8/12 是 32.516883（Yahoo 為 32.5169）
      - 線上定義：https://help.yahoo.com/kb/SLN28256.html 說明 adjusted close 是「the closing price after adjustments for all applicable splits and dividend distributions」；https://en.wikipedia.org/wiki/Ex-dividend_date 寫道「The opening price on the ex-dividend date... can be expected to decrease by the amount of the dividend」
      - 自選股（s4_watch.out）：窗口內 TWT49U 只有 6196(6/24)、2454(7/07)、3034(7/10)、0050(7/21,0.6%)、3545(8/04)、00878(8/18)、2379(9/08) 有除權息紀錄，00885、00893 沒有。多頭排列只在還原價成立的天數：00878 8 天（8/19、8/20、8/27～9/03）、3034 7 天（7/31～8/10）、0050 1 天、6196 1 天。0050 在 8/03 反而是還原價才有 MA20 跌破
      - 6669（s4_watch.out）：9/01 收盤 7800 → 9/02 收盤 2610（TWT49U ex_ref 2614.99，跌 66.47%）；9/02 未還原出現 MA20 跌破，布林下軌跌破持續 9/02～9/04；9/23 的 MA60 未還原 4860.17 vs 還原 2037.72，low_60d 2105 vs 1548.88，momentum_60d −54.6% vs +35.5%
      - 9/23 多出的 18 檔名稱依 TWT49U：00923 是群益台ESG低碳50，00770 是國泰北美科技，00881 是國泰台灣科技龍頭；高息型 ETF 只有 00918、00919、00961、00964；另有 8 檔普通股
      - 腳本與輸出都在 AUDIT_SCRATCH/work/verify-trend-unadj/（s1_load.py、s2_events.py/.out、s3_picks.py/.out、s4_watch.py/.out、s5_yahoo.py、yahoo_00878.json）；repo 的 git status 與開始時相同

### [P2 → P2] consolidation_breakout／swing 混用時間軸 rolling_max/min 與 valid_shift：缺一根 K 棒就在 20 根內完全不觸發，之後再用過期區間產生假訊號（部分成立）

- **主張**：rolling_max/rolling_min 在共用時間軸上取 20（或 2r+1）列，窗內只要有 NaN 結果就是 NaN；valid_shift 只在來源有值的列輸出，而且會跳過 NaN 取更早的值。兩者疊加後，缺資料的標的在窗內期間 base_high=NaN（不可能觸發），窗口清空的那一天則拿到缺口之前的舊區間。
- **正確的定義或數值**：區間高低點應以『前 N 根有效 K 棒』計算（與 valid_rolling_mean、valid_shift 同一語意），缺一天不應讓策略失效或引用過期區間。
- **app 實際行為**：缺一根 K 棒後 20 根內不會觸發，窗口清空當天改用缺口前的舊區間（推測：上櫃 9/16、9/23 缺檔在約 10 月中旬窗口清空時，會出現一批以 9/15 舊區間判定的假突破）。
- **對使用者的影響**：停牌過或資料缺日的股票，整理突破／波段訊號會漏報或誤報；目前上櫃全市場都受影響。
- **修法建議**：改用 valid_rolling_max/valid_rolling_min（matrix.py 已有，與 high_60d 相同）取代 rolling_max/rolling_min。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/consolidation_breakout.py:263-264（shift(rolling_max(market.high, base_window), 1)）
  - backend/app/strategy/builtin/swing_trend_confirmation.py:37-40
  - backend/app/backtest/matrix.py:3012-3029（rolling_min/max 走 _rolling_reduce）
  - backend/app/backtest/matrix.py:3465-3470（sliding_window_view＋np.max，NaN 會傳染）
  - backend/app/backtest/matrix.py:2949-2953（valid_shift 跳過非有限值）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - app_run.py：9/17～9/22 app 的 consolidation 選股中 TPEX 為 0；依『前 20 根有效 K 棒』獨立實作應有 30 筆上櫃（例如 6026、3580、5347）
  - debug_tpex.py：6026.TPEX 9/17 除 compact/breakout 之外各項條件都成立，但 base_high=NaN；真實前 20 根高低為 15.45/14.4
  - debug_diffs.py：6176.TWSE 9/18 app base_high=89.5（取自 8/12 之前的窗），實際前 20 根有效 K 棒最高 108 → app 產生假的整理突破（該檔 8/13～8/21 無 K 棒）
  - swing 在 9/17～9/22 有 12 筆 TPEX 進場差異
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：consolidation_breakout（consolidation_breakout.py:103-104）與 swing_trend_confirmation（swing_trend_confirmation.py:37-40）在共用時間軸上用 rolling_max／rolling_min，再接 valid_shift，與同一份策略的 valid_rolling_mean、KDJ 的 valid_rolling_min/max 口徑不一致。

某標的在第 g 列缺 K 棒時，之後 19 根有效 K 棒的區間高低點都是 NaN，整理突破不可能觸發。窗口清空的那一根（g+20，停牌多日則更晚），會引用缺口前的舊 20 日區間。實測 6176.TWSE 在 9/18 因此產生假突破：區間上緣 89.5，正確值 108。swing 缺口附近約 4 個候選波段點無法確認。

上櫃 9/16、9/23 整個市場缺檔，因此 9/17–9/22 app 的整理突破沒有任何上櫃股；valid-bar 口徑應有 51 筆。swing 有 12 筆上櫃進場差異。自選 9 檔目前不受影響。

修法：改用 valid_rolling_max／valid_rolling_min，以 isfinite(close) 搭配 isfinite(high/low) 作為 mask。
    - 理由：我讀了 finding 引用的程式碼，在合成資料和真實資料上各重現一次，並對照外部定義。finding 的核心主張成立。

一、程式碼機制
1. consolidation_breakout 在 103-104 行（finding 寫 263-264，行號有誤）以 valid_shift(rolling_max(market.high, base_window), 1) 計算區間上下緣。
2. rolling_max／rolling_min 透過 _rolling_reduce，在共用時間軸上用 sliding_window_view 加 np.max／np.min 取值，實測 np.max([1, nan, 3]) = nan。所以窗內只要有一列是 NaN，結果就是 NaN。
3. valid_shift 的 kernel 遇到來源不是有限值的列會 continue 跳過，只在有值的列輸出，取值來自前一個有值的列。
4. 兩者疊加的結果：
   - 缺口所在列 g 之後的 19 根有效 K 棒，base_high 都是 NaN，突破條件不可能成立。
   - 在 g+20 那一根，valid_shift 跳過中間所有 NaN，取到 g-1 的窗（g-20..g-1），也就是缺口前的舊區間，忽略缺口後的 19 根 K 棒。
   - 舊區間只影響「窗口清空當天」這一根；如果停牌多日，那一根會更晚才出現。
5. swing 也有同樣的錯配：_confirmed_swings 用 rolling_max 在時間軸上取 2r+1 列，候選點卻用 valid_shift 以有效 K 棒計算。缺口附近的窗是 NaN，所以缺口前後約 4 個候選波段點無法確認。
6. 同一份策略的量能均值（108 行）用的是 valid_rolling_mean，repo 內 KDJ 的 9 日高低點（matrix.py:3923-3924）也用 valid_rolling_min／max。valid_shift 的 docstring 則明寫缺 K 棒的列要當作不存在。所以 finding 建議的語意，與 repo 自己的慣例一致。

二、外部定義
StockCharts、TradingView、Wikipedia 都把通道上下緣定義為「最近 N 個期間（N-day）的最高價／最低價」，並排除當期。期間指的是該商品序列上的 K 棒。沒有任何定義會因為某一天缺資料，就讓之後 20 天都無值，或回頭引用 27 天前的舊區間。

三、實測（真實 enriched 資料，2026-02-02 至 09-23，155 列 × 2345 檔）
1. 上櫃在 9/16 與 9/23 整個市場缺檔。kline_daily_enriched 和 taiwan/daily 兩份資料這兩天的 TPEX 筆數都是 0。
2. consolidation 用原版計算，9/17–9/22 的上櫃進場數是 0；只把 rolling_max／rolling_min 換成 valid_rolling_max／valid_rolling_min，上櫃進場數為 9、14、21、7，共 51 筆。finding 寫 30 筆，數字不同，但方向一致。
3. 6026.TPEX 在 9/17：app 的 base_high 是 NaN，前 20 根有效 K 棒的最高價是 15.45。
4. 6176.TWSE 在 9/18：該檔 8/13–8/21 缺 7 根 K 棒。app 的 base_high 是 89.5，正確值是 108，當天收盤 96.1。所以 app 多出一筆假的整理突破（只有原版有、修正版沒有）。
5. swing 在 9/17–9/22 的上櫃進場差異共 12 筆（3+2+4+3），與 finding 一致。

四、使用者情境
1. 自選 9 檔都是上市股，近 45 列都沒有缺檔，實測 base_high 與正確值相同（2454、6196、3545），目前不受影響。
2. 選股、雷達、回測的全市場結果會受影響：
   - 目前 20 多個交易日內，上櫃幾乎不可能出現整理突破。
   - 2 月以來，扣掉 9/16、9/23 之外，另有 90 檔上市與 237 檔上櫃有個股缺 K 棒。這些股票都會遇到同樣的漏報，以及那一天的誤報。

五、嚴重度
這是明確的功能錯誤，只影響有缺 K 棒的標的，不破壞資料，也不影響自選股，P2 恰當。不過，現在全上櫃市場受影響，是另一個資料缺檔問題放大的結果。

需要更正或補充的地方：
1. 行號要改成 103-104。
2. 上櫃應有進場筆數，我的重現是 51 筆。
3. 引用舊區間只發生在窗口清空的那一根 K 棒。
4. 預測的舊區間假突破：假設 9/16、9/23 都沒有回補，會出現在 9/23 之後約 20 個交易日，大約 10 月下旬，而不是 10 月中旬（推測）。
5. 另外發現（finding 沒提）：bottom_reversal.py:100、136，pullback_resume.py:97，lorentzian_knn.py:113-114 也在共用時間軸上用 rolling_max／rolling_min，推測有同類問題，未逐一驗證。
    - 證據：
      - backend/app/strategy/builtin/consolidation_breakout.py:103-104：base_high = shift(rolling_max(market.high, base_window), 1)；base_low 同理（finding 寫 263-264，行號不對）
      - backend/app/strategy/builtin/consolidation_breakout.py:108：同一份策略的量能均值用 valid_rolling_mean，口徑與區間高低點不一致
      - backend/app/strategy/builtin/swing_trend_confirmation.py:37-40：window = rolling_max/min(values, 2*radius+1)（時間軸），candidate = valid_shift(values, radius)（有效 K 棒軸）
      - backend/app/backtest/matrix.py:3012-3029、3465-3470：rolling_min/max → _rolling_reduce → sliding_window_view + np.max/np.min；實測 np.max([1, nan, 3]) = nan
      - backend/app/backtest/matrix.py:2949-2953：valid_shift kernel 遇到非有限值就 continue，取前一個有值的列
      - backend/app/backtest/matrix.py:2976-2981：valid_shift docstring 寫 'Standard per-symbol indicators must treat those rows as absent rather than as observations.'
      - backend/app/backtest/matrix.py:3923-3924：KDJ 的 9 日高低點用 valid_rolling_min/valid_rolling_max（repo 內的既有慣例）
      - 合成測試 synth.py（scratchpad\audit\work\verify-rollgap-def\）：第 24 列缺值、第 5 列有 99 的尖峰；app 的 base_high 在第 25–43 列全是 NaN，第 44 列回到 99.0（缺口前的舊窗）；valid-bar 版本在第 44 列是 12.0
      - counts.py：kline_daily_enriched 在 date=2026-09-16 與 2026-09-23 的 TPEX 都是 0 筆（TWSE 1319/1323），taiwan/daily 同樣是 0；其他日約 975–993 筆
      - real.py（原版策略 vs 以 mock 把 rolling_max/min 換成 valid_rolling_max/min）consolidation 上櫃進場數：09-17 為 0 vs 9、09-18 為 0 vs 14、09-21 為 0 vs 21、09-22 為 0 vs 7；只有原版出現的是 09-18 的 6176.TWSE
      - real.py：6026.TPEX 在 2026-09-17，app base_high = nan，valid-bar 版本 = 15.45，手算前 20 根有效最高 = 15.45，收盤 15.5
      - real.py：6176.TWSE 在 2026-09-18，app base_high = 89.5，valid-bar 版本 = 108.0，手算 = 108.0，收盤 96.1；缺 8/13、8/14、8/17–8/21 共 7 根
      - real.py：swing 上櫃進場差異 09-17 為 3、09-18 為 2、09-21 為 4、09-22 為 3，合計 12 筆
      - real.py：自選 2454、6196、3545 在 09-22 的 app base_high 與 valid-bar 版本相同（5035/549/53.4），近 45 列都沒有缺檔
      - gaps.py：2026-02 以來，扣掉 9/16、9/23 之外，另有 90 檔上市與 237 檔上櫃有序列中間缺 K 棒
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels：'Upper Channel Line: 20-day high'；'Price Channels are based on prices prior to the current period.'
      - https://en.wikipedia.org/wiki/Donchian_channel：upper band 為 'taking the highest high...of the last n periods'
      - https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/：'Upper Channel = 20 Day High'、'Lower Channel = 20 Day Low'
      - 推測（未驗證）：bottom_reversal.py:100、136，pullback_resume.py:97，lorentzian_knn.py:113-114 也用共用時間軸的 rolling_max/min，可能有同類問題
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：consolidation_breakout.py:103-104 與 swing_trend_confirmation.py:37 在共用時間軸上用 rolling_max／rolling_min 取 20 列（波段策略是 2r+1 列），窗內只要有 NaN，結果就是 NaN。valid_shift（matrix.py:2949-2953）又會跳過 NaN、改取更早的有效值。兩者疊加後，缺 K 棒的標的會先有約 20 列 base_high／base_low 為 NaN，期間完全不觸發；窗口清空那天，valid_shift 取到的是缺口前最後一個有效窗口，也就是過期區間。

本地重現結果：
- 上櫃 9/16、9/23 整天缺資料，但官方資料顯示這兩天都有交易。9/17～9/22 app 的整理突破上櫃筆數是 0；以有效 K 棒計算應有 51 筆（依日期 9、14、21、7），去重後 41 檔，其中 2 檔是 ETF。finding 寫的 30 筆不正確。
- 波段策略在同期間有 12 筆上櫃進場差異，漏報與多報都有。
- 上市個股也受影響：6176 在 9/18 用 7/16～8/12 的舊區間（最高 89.5）判定出假突破，正確值應為 108；00683L 在 9/17、2945 在 9/21 漏報。
- 只把 rolling_max/min 換成 valid_rolling_max/min，結果就與獨立實作逐檔相同。

補充：如果上櫃 9/16、9/23 的資料一直不補，app 要到 9/23 之後第 20 根 K 棒（推測約 10 月下旬）才恢復，而且當天會拿 8/19～9/15 的舊區間判斷突破。6176 的缺口是 8/24 減資換股造成的真實停牌，改用有效 K 棒只能消除這筆假訊號；窗口仍混用減資前未調整的價格，這屬於另一個還權問題。
    - 理由：核心機制重現了，也是這次差異的唯一來源：兩支策略的 rolling_max／rolling_min 是在共用時間軸上取窗口，窗內只要有 NaN，結果就是 NaN；valid_shift 又會跳過 NaN，改取更早的有效值。兩者疊加，就出現 finding 描述的兩種錯誤：漏報與引用過期區間。

驗證方法：
- 用 kline_daily_enriched（app 的 enriched 正本）組 2025-12-01～2026-09-23 的 panel。
- 分別以 101 根與 180 根歷史視窗建 MarketDataMatrix，兩者結果完全相同。
- 比對三種結果：(a) app 原碼經 MatrixStrategyPipeline 的輸出；(b) 只把 rolling_max/min 換成 valid_rolling_max/min 的版本；(c) 我自己用 polars 按個股有效 K 棒寫的獨立實作。
- 9/17～9/23 每一天，(b) 與 (c) 的整理突破名單都逐檔相同。所以 app 與正確語意之間的差異，全部來自 rolling_max/min。

數值對照（finding → 我的結果）：
- app 在 9/17～9/22 的整理突破上櫃筆數：0 → 0，重現。
- 獨立實作的上櫃筆數：「應有 30 筆」→ 我算出 51 筆，依日期為 9、14、21、7；去重後 41 檔，其中 2 檔是 ETF（00888、009815）。這項數字沒有重現，實際影響比 finding 寫的大。
- 6026.TPEX 在 9/17：app 的 base_high 是 NaN，真實前 20 根的高／低是 15.45／14.4，與 finding 一致。獨立實作中 9 項條件全部成立，所以 app 只是被 NaN 擋掉。
- 6176.TWSE 在 9/18：app 的 base_high 是 89.5，取自 7/16～8/12 的窗口；真實前 20 根最高是 108。app 因此多出一筆假的整理突破，與 finding 一致。
- 波段策略在 9/17～9/22 的上櫃進場差異：12 筆 → 12 筆（依日期 3、2、4、3），重現。其中既有 app 漏報，也有 app 多報，例如 6683、3066、6015、3219、6182 只出現在 app 結果裡。

需要更正的地方：
1. consolidation_breakout.py 的行號寫錯。實際是 103-104 行，不是 263-264 行；matrix.py 的行號正確。
2. 過期區間出現的時間點。上櫃在 9/23 也缺資料，所以 NaN 會一路延續到 9/23 之後的第 19 根，要到第 20 根才出現缺口前的舊區間，不是「9/16 起算約 10 月中旬」。我用 6026 的實際序列加 25 根合成 K 棒模擬：第 20 根時 app 的 base_high 是 15.45，也就是 8/19～9/15 的窗口；正確值應為 16.15。依台灣假日推算大約是 10 月下旬，這點是推測。
3. 6176 的缺口是真實停牌。HiStock 顯示「2026-08-24 減資後新股掛牌」，本地 8/12 收 81.3，8/24 開 102.5。所以 finding 建議的修法（valid_rolling）雖然能消除 9/18 的假訊號，但窗口裡仍混著減資前未調整的價格（base_low 81.0 來自 8/12）。這屬於另一個還權問題，不在本 finding 範圍內。
4. 上市股票也受影響，不只上櫃：00683L（缺 8/18、8/28）在 9/17 漏報，2945（缺 9/8）在 9/21 漏報，6176 在 9/18 誤報。

官方資料確認上櫃 9/16、9/23 都有交易：櫃買指數 9/16 收 399.21、9/23 收 413.76；6488 與 8069 這兩天也都有成交。本地 kline_daily_enriched 與 taiwan/daily 這兩天的上櫃筆數都是 0，所以缺口是本地資料缺漏。

嚴重度維持 P2：這是明確的策略功能錯誤，會造成整批漏報與個別誤報，但不會破壞資料本身。
    - 證據：
      - backend/app/strategy/builtin/consolidation_breakout.py:103-104：base_high = shift(rolling_max(market.high, base_window), 1)／base_low = shift(rolling_min(market.low, base_window), 1)。finding 寫的 263-264 行錯誤，檔案只有 149 行
      - backend/app/strategy/builtin/swing_trend_confirmation.py:37-38：window = rolling_max/rolling_min(values, 2*radius+1)；candidate = shift(values, radius)，這裡的 shift 就是 valid_shift
      - backend/app/backtest/matrix.py:3012、3022：rolling_min、rolling_max 都走 _rolling_reduce；3465-3470：sliding_window_view 加 np.min/np.max，窗內有 NaN 就回傳 NaN
      - backend/app/backtest/matrix.py:2949-2953：valid_shift 的 kernel 遇到非有限值就 continue，輸出取 ring 內更早的有效列
      - backend/app/backtest/matrix.py:2712-2734：_encode_axes 用全 panel 的聯集日期當時間軸，所以上櫃缺日會在共用軸上變成 NaN 列
      - 本地資料：kline_daily_enriched 在 2026-09-16 與 2026-09-23 的 TPEX 筆數都是 0（TWSE 分別為 1319、1323）；taiwan/daily 相同（上櫃 0 筆）。指令：uv run python verify-rollgap/probe.py 與 repro.py 的第一行輸出
      - 官方參考資料 csv/index_daily_202608_202609.csv：TPEX_INDEX 2026-09-16 收 399.21、2026-09-23 收 413.76；csv/tpex_trading_stock_202608_202609.csv：6488 與 8069 在 9/16、9/23 都有成交，證明兩天都是上櫃交易日
      - 重現輸出 verify-rollgap/repro_101.txt 的整理突破（上市, 上櫃）筆數，依序為 app／改用 valid_rolling／獨立實作：9/17 (17,0)/(18,9)/(18,9)；9/18 (37,0)/(36,14)/(36,14)；9/21 (31,0)/(32,21)/(32,21)；9/22 (23,0)/(23,7)/(23,7)。每天 fix 版與獨立實作都相同（equal: True）；180 根歷史視窗（repro_180.txt）結果一致
      - 6026.TPEX 9/17：app base_high=nan、base_low=nan；獨立實作 base_high/low=(15.45, 14.4)，close=15.5，9 項條件全為 True
      - 6176.TWSE 9/18：app base_high=89.5、base_low=80.7；獨立實作 base_high/low=(108.0, 81.0)，close=96.1，breakout=False。cb only_in_app 為 ['6176.TWSE']，代表 app 產生了假突破。check6176.py 輸出：7/16～8/12 的 20 根最高 89.5；8/12～9/17 的 20 根最高 108.0；本地缺 2026-08-13、14、17、18、19、20、21
      - 波段策略的 app 與 fix 差異：9/17 ['5347','6620','6683'] 3 筆；9/18 ['3066','5230'] 2 筆；9/21 ['3354','3624','6015','6016'] 4 筆；9/22 ['3219','6182','6207'] 3 筆，都是 TPEX，合計 12 筆
      - sim_future.py：6026 在共用軸上 9/16、9/23 是 NaN，後面接 25 根合成 K 棒。app 的 base_high 要到 9/23 之後第 20 根才恢復為 15.45（8/19～9/15 的窗口）；同一列的有效 K 棒值是 16.15
      - https://histock.tw/stock/6176（WebFetch）：頁面行事曆寫「2026-08-24 減資後新股掛牌」，9/23 收盤 92.8，與本地資料相同，證明 6176 在 8/13～8/21 是減資停牌而非資料缺漏
      - 上市股票也受影響：00683L.TWSE 本地缺 8/18、8/28，在 9/17 只出現在獨立實作的結果裡；2945.TWSE 本地缺 9/8，在 9/21 只出現在獨立實作的結果裡

### [P2 → P2] macd_golden 單獨執行（run_preset）只用 61 根暖機，約 18% 的金叉判定與全歷史 MACD 不同，而且會覆寫快取（部分成立）

- **主張**：required_warmup_bars 只宣告 60，單獨執行時 build_strategy_context 只載入 61 根，EMA26 與 DEA 以窗首收盤價當初值；run_preset 的結果會用 _update_cache_strategy 寫回 strategy_cache。另外 warmup 不會逐檔強制，上市不到 60 根的標的也會被選入。
- **正確的定義或數值**：MACD 暖機至少 250 根，或要求每檔至少有足夠的有效 K 棒；單獨執行與 run_all 應產生同一結果。
- **app 實際行為**：同一天、同一策略，從策略頁單獨執行與盤後批次的結果不同，後執行的一方會覆寫快取。
- **對使用者的影響**：使用者在策略頁按一次執行，儀表板卡片的 MACD 金叉清單就可能換成暖機不足的版本；新上市 ETF 以極短歷史觸發金叉。
- **修法建議**：把 macd_golden（以及其他使用 EMA 的策略）的 required_warmup_bars 提高到 ≥250，或直接改用 enriched 預算好的 macd_dif/macd_dea（全歷史）；進場另加 valid bars ≥ 暖機長度的條件。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/macd_golden.py:55-57（warmup 60）
  - backend/app/strategy/builtin/macd_golden.py:64-69
  - backend/app/strategy/engine.py:675-684
  - backend/app/api/screener.py:284-304（run_preset 只帶 [strategy_id]）
  - backend/app/api/screener.py:198-209（_update_cache_strategy 覆寫快取）
- **線上證據**：
  - StockCharts：『for a 100% accurate EMA, you should use every data point…』、『On our charts, we calculate back at least 250 periods』 https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential
- **本地計算證據**：
  - macd_warmup2.py：9/3～9/23 每天各用『截至當日的 62 個交易日』窗計算，與全歷史 MACD 比對，選股 292 對 299，不一致 53 筆（例如 9/23 漏掉 2618、誤選 9910）
  - 同一檢查中 run_all 的 122 日窗只差 1 筆（float32）
  - strategy_cache 9/23 的 macd_golden 選出 00408A（上市 51 根，ma60、momentum_60d 皆為 None，score=0）；15 天內有 5 筆選股少於 60 根，最少只有 14 根（00409A，9/21）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：macd_golden 只宣告 60 根暖機，單獨執行時（/api/screener/run_preset 或 /api/strategy/run）只載入 62 個交易日。EMA26 與 DEA 以窗首的值當初值，EMA26 的初值殘留權重約 0.9%。實測 9/3～9/23 與全歷史 MACD 相比有 53 筆選股不同（292 對 299）；run_preset 還會把這個結果寫回 strategy_cache。不過前端目前沒有任何畫面呼叫 run_preset 或 strategy/run。使用者看到的快取由盤後 compute_and_cache_all 以 122 根窗產生，15 天只差 1 筆，所以這部分在使用者情境下只能從 API 直接觸發，屬 P3 潛在問題。

真正影響使用者的是另一個面向：矩陣策略既不逐檔檢查有效 K 棒數是否達到暖機長度，DEFAULT_BASIC_FILTER 宣告的 exclude_new_days=30 在整個 app 也沒有實作。結果盤後快取在 15 天內有 5 筆選股的歷史少於 61 根，例如 00409A 只有 14 根（首筆資料 9/2，9/21 被選入）、00408A 有 51 根（ma60 與 momentum_60d 為 None，score=0）。這是 P2 的功能錯誤。

修法：提高 EMA 類策略的暖機長度，或改用 enriched 預先算好、以全歷史計算的 macd_dif／macd_dea；並逐檔要求有效 K 棒數 ≥ 暖機長度，同時實作 exclude_new_days。
    - 理由：程式碼和數值上的主張都成立，而且我從頭獨立重算，數字完全吻合。但「使用者在策略頁按一次執行，就會覆寫儀表板快取」這段影響不成立：前端沒有任何畫面呼叫 run_preset。

一、成立的部分（親自讀程式碼並實跑）：
(1) macd_golden 的 required_warmup_bars 回傳 60，因此 required_history_bars＝61。repository.get_enriched_history 取 trading_dates[-(61+1)]，所以單獨執行時矩陣只有 62 個交易日。
(2) _valid_ewm_kernel 以窗內第一個有效值當 EMA 初值（matrix.py:3381），DEA 也一樣。EMA26 經過 61 步後，初值殘留權重是 (25/27)^61＝0.0091。
(3) run_preset 會呼叫 _update_cache_strategy。只要快取的 as_of 相同，就覆寫該策略的快取結果。
(4) MatrixStrategyPipeline 不會逐檔檢查 warmup。另有 finding 沒寫到的新面向：DEFAULT_BASIC_FILTER 宣告了 exclude_new_days=30，但整個 app 沒有任何地方實作它，所以宣告的新股排除條件是失效的死設定。
(5) 我自己寫腳本獨立重算（沒用 finding 的腳本），跟 finding 的數字完全一致：9/3～9/23 共 15 個交易日，全歷史選出 292 筆、62 根窗選出 299 筆，不一致 53 筆；122 根窗只差 1 筆。9/23 那天，62 根窗漏掉 2618、誤選 9910。另有 5 檔在歷史少於 61 根時被選入，包括 00409A（9/21，只有 14 根）與 00408A（9/23，51 根）。只看金叉本身、還沒套量比過濾時，1488 個金叉裡有 492 個判定不同。
(6) 權威來源：TA-Lib 官方文件（與 StockCharts 不同的來源）說明 EMA 以資料起點為種子，種子影響會逐根遞減，而且 MACD 也受影響。這支持「62 根暖機不足、結果取決於窗口起點」的判斷。

二、不成立或需修正的部分：
(1) 前端只在 frontend/src/lib/api.ts:2526 定義了 screenerRunPreset，沒有任何地方呼叫它（打包後的 dist bundle 也只有這個定義）。Strategies.tsx 只呼叫 screenerStrategies 和 screenerCached。
(2) 使用者看到的快取由 daily_pipeline 的 compute_and_cache_all 寫入。它把所有策略放在同一個 context 裡，暖機取最大值 121＋1＝122 根，與全歷史幾乎一致。
(3) 監控規則是 0 條，所以即時監控不會覆寫這個策略的結果。
結論：在使用者情境下，只有直接打 HTTP API（/api/screener/run_preset，或不寫快取的 /api/strategy/run）才會觸發「單獨執行結果不同並覆寫快取」，屬於潛在問題。

三、嚴重度：
- 單獨執行與覆寫快取這部分應降為 P3：只能從 API 觸發，UI 碰不到。
- 「不逐檔強制暖機、exclude_new_days 沒實作，導致新上市標的以極短歷史被選入」這部分，確實出現在使用者儀表板的快取裡（15 天內 5 筆，最少只有 14 根）。而且它違反了程式自己宣告的預設過濾條件，屬於明確的功能錯誤，維持 P2 合理。
- 整體維持 P2，但成立理由要改寫。
    - 證據：
      - backend/app/strategy/builtin/macd_golden.py:55-57：required_warmup_bars 回傳 60；第 64-69 行對 close 做 EMA12／EMA26，再對 DIF 做 EMA9。
      - backend/app/strategy/engine.py:674-684：required_history_bars＝max(scoring_warmup, warmup+1)＝61。
      - backend/app/repository.py:1186：lookback_start = trading_dates[-(lookback_days + 1)]，也就是 62 個交易日。
      - backend/app/backtest/matrix.py:3378-3383：第一個有效值直接當初值（state = float(value)），之後才遞迴。
      - backend/app/api/screener.py:198-209、263、287、304：run_preset 只傳 [req.strategy_id]，之後呼叫 _update_cache_strategy，在 as_of 相同時覆寫 results[strategy_id]。
      - backend/app/api/screener.py:600-613 與 app\jobs\daily_pipeline.py:92：盤後批次 compute_and_cache_all 對所有策略共用一個 context，暖機取最大值（lorentzian_knn.py:127 的 60+60，因此是 122 根）。
      - backend/app/strategy/engine.py:52 宣告 exclude_new_days: 30；在 app 內 grep exclude_new_days，只有這一行。matrix.py:3641-3673 的 _build_basic_filter_mask_uncached 沒有處理它，Pipeline（matrix.py:3530-3580）也沒有逐檔的暖機檢查。
      - 反證（UI 路徑）：screenerRunPreset 只定義在 frontend\src\lib\api.ts:2526；在整個 frontend/src grep screenerRunPreset、screenerRun 與 strategyRun，都沒有呼叫點。frontend\dist\assets\router-BtwRXlnr.js 也只有這個定義。frontend\src\pages\Strategies.tsx:59、64 只呼叫 screenerStrategies 和 screenerCached。
      - 本地重算（我自己寫的獨立腳本）：scratchpad\audit\work\verify_macd_warmup_def\macd_cmp.py，資料取自 kline_daily_enriched 全部分區（2024-01-02～2026-09-23，2346 檔）。輸出為：totals {'w62': 299, 'w122': 291, 'full': 292}、mismatch w62 53、mismatch w122 1；9/23 的差異是 ('2618.TWSE','full')、('9910.TWSE','w62')。只看金叉（未套量比）時，全歷史共 1488 個金叉，62 根窗有 492 個不同。
      - 同一支腳本另外輸出歷史不足 61 根就被選入的清單：('2026-09-04','009824.TWSE',51)、('2026-09-17','00990B.TPEX',49)、('2026-09-21','00409A.TWSE',14)、('2026-09-22','7855.TWSE',31)、('2026-09-23','00408A.TWSE',51)。00409A 的第一筆資料是 2026-09-02，距 9/21 只有 19 天，照理應被 exclude_new_days=30 排除。
      - data\user_data\strategy_cache.json（as_of 2026-09-23）：macd_golden total=10，含 2618、不含 9910、含 00408A，而且 00408A 的 ma60=None、momentum_60d=None、score=0.0。這與批次（122 根窗）的結果一致，所以目前的快取不是 run_preset 寫入的。
      - enriched 預先算好的 signal_macd_golden 與我用全歷史重算的金叉：9/22 在 2316 檔中不一致 0 檔，9/23 在 1323 檔中不一致 0 檔。可見修正建議「改用 enriched 的 macd_dif／macd_dea」可行。
      - 初值殘留權重（uv run python 實算）：(25/27)^61＝0.00914、(11/13)^61＝3.75e-05、(25/27)^250＝4.4e-09。
      - TA-Lib 官方文件 https://ta-lib.org/api/unstable-period/ ：『Some indicators have "memory". Each output depends on the previous one, seeded from the start of the data.』『the seed's effect is large at first, and decays with each bar until the result is stable.』『The EMA id therefore affects EMA itself and every indicator built on one, such as MACD and DEMA.』
      - TA-Lib 官方文件 https://ta-lib.org/api/ （4.2 Numerical Stability）：『Your value changed when you fed the same bar more history? That is by design: recursive functions converge as history accumulates.』
      - Wikipedia 的 Exponential smoothing 條目 https://en.wikipedia.org/wiki/Exponential_smoothing ：『the initial value of demand will have an unreasonably large effect on early forecasts』；α 越小，『the more sensitive the forecast will be on the selection of this initial smoother value s₀』。
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：macd_golden 的 required_warmup_bars 宣告 60。單獨執行（/api/screener/run_preset）時，實際載入的是 62 個交易日，不是 61（repository.py:1184-1189）。EMA12、EMA26 與 DEA 都以窗首的值當初值（matrix.py:3378-3382）。

以 9/3～9/23 共 15 個交易日重算：
- 62 根窗選出 299 筆，全歷史選出 292 筆，兩者不一致 53 筆，約佔全歷史選股的 18%。
- 只看金叉判定本身（不加量比過濾），兩者差 492 筆，全歷史金叉共 1488 筆。
- 9/23 單獨執行會誤選 9910、漏掉 2618；9/18 會漏掉自選股 3545。
- run_all（122 根窗）與全歷史只差 1 筆，與儀表板實際的快取結果完全一致。

run_preset 的結果會經 _update_cache_strategy 覆寫同一天的快取，但前端沒有任何地方呼叫 run_preset：screenerRunPreset 只有定義，策略頁與儀表板都只讀快取。因此「在策略頁按執行就會換成暖機不足版本」這條路徑不存在。這個差異只在直接呼叫 API 時才會發生，是潛在問題，約 P3。

會實際影響儀表板的，是逐檔暖機沒有強制執行。不論用哪種窗，15 天內都有 5 筆選股少於 60 根：00409A 只有 14 根（9/21）、7855 有 31 根、00408A 有 51 根（9/23，score=0，ma60 與 momentum_60d 都是 None）。另外，引擎預設篩選條件 exclude_new_days: 30（engine.py:52）完全沒有被實作，本來可以排除這類剛上市的標的。這部分維持 P2。

修法：
- 對每檔標的要求有效 K 棒數至少達到暖機長度，並實作 exclude_new_days。
- 把 EMA 類策略的暖機提高，或改用 enriched 裡全歷史的 macd_dif 與 macd_dea，讓單獨執行與 run_all 的結果一致。
    - 理由：finding 的數值我全部重現了，連個別股票代號都一樣；問題在「使用者影響」那段寫錯了。

【我怎麼驗證】
我寫了 step2_repro.py，用 app 自己的 compute_indicators、StrategyEngine.run 和 build_market_data_matrix 重算，輸入是本地 parquet 全部 661 個交易日。9/3 到 9/23 共 15 個交易日，每天跑三種歷史窗：
- A：單獨執行的窗，62 根。
- B：run_all 的窗，122 根。
- C：全歷史，647 到 661 根。

為了確認模擬忠實，我拿 B 在 9/23 的結果對照實際的 data/user_data/strategy_cache.json：10 檔選股的集合與順序完全相同。另外把本地 close、raw_close 對官方 TWSE STOCK_DAY 參考檔（10 檔、8～9 月共 380 列），收盤價 0 筆不符，所以輸入資料沒有問題。

【數字並列（finding 的數字 / 我的數字）】
- 選股總數「292 對 299」/ C=292、A=299，完全相同。
- 不一致「53 筆」/ A 與 C 的對稱差 = 53，相同。
- 9/23「漏掉 2618、誤選 9910」/ A 有 9910、沒有 2618，而 B、C 都有 2618，相同。
- 「run_all 122 日窗只差 1 筆」/ B 與 C 差 1 筆（9/22 的 3483.TPEX），相同。
- 「5 筆選股少於 60 根，最少 14 根（00409A，9/21）；00408A 51 根」/ 我算出的 5 筆是 009824 51 根（9/4）、00990B 49 根（9/17）、00409A 14 根（9/21）、7855 31 根（9/22）、00408A 51 根（9/23），完全相同。00409A 的第一根是 2026-09-02，00408A 的第一根是 2026-07-15。快取裡 00408A 的 score=0、ma60 和 momentum_60d 都是 None，也與 finding 相符。
- 「約 18%」：這是最終選股的差異比例（53/292≈18.2%）。如果只看 MACD 金叉判定本身、不加量比過濾，A 與 C 的對稱差是 492 筆，全歷史金叉共 1488 筆，差異其實更大。
- 另外找到一個與使用者相關的例子：自選股 3545.TWSE 在 9/18，全歷史與 run_all 都選入，62 根的單獨執行漏掉了。

【程式碼機制】
- 暖機宣告：macd_golden.py:55-57 回傳 60；engine.py:683 加 1，得到 61。
- 實際載入根數：repository.py:1184-1189 取 trading_dates[-(61+1)]，所以單獨執行實際是 62 根，不是 finding 說的 61 根。這點是小誤差。
- EMA 起算點：matrix.py:3378-3382 用第一個有效值當 EMA 初值，也就是用窗首的收盤價起算，finding 說得對。
- run_all 的窗：它的 history_bars=121（step1 輸出）。這個值讓 repo 快取的覆蓋檢查失敗，改走 services/screener.py:248-249 的慢路徑，讀 180 天後裁成 122 根。
- 覆寫快取：run_preset 會經 api/screener.py:302 呼叫 _update_cache_strategy（198-209），而且只在快取的 as_of 相同時才覆寫。

【需要修正的部分】
1. 使用者影響說錯了。finding 說「使用者在策略頁按一次執行，快取就會被覆寫」，但前端根本沒有地方呼叫 run_preset。api.ts:2526 只定義了 screenerRunPreset，全前端找不到任何呼叫者。Strategies.tsx:63-64 與 Dashboard.tsx:422-423 都只讀 screenerCached。後端呼叫 run_preset 的也只有測試。所以 62 根窗的差異，以及它覆寫快取的問題，只有直接呼叫 HTTP API 時才會發生，屬潛在問題，只到 P3。儀表板卡片實際來自 run_all（122 根），15 天內與全歷史只差 1 筆。
2. 短歷史標的被選入的問題是真的，而且三種窗都會發生，與單獨執行無關，會直接出現在儀表板的快取結果裡。另外找到一個原記錄沒提到的面向：DEFAULT_BASIC_FILTER 裡宣告的 exclude_new_days: 30（engine.py:52）在整個 app 裡沒有任何地方讀取，是失效的設定，本來可以排除 00409A 這類 14 根的標的。

【整體判定】
數據重現成立。finding 描述的使用者觸發路徑不成立。維持 P2 的理由只剩「逐檔暖機與新上市排除沒有強制執行」這一部分。

【線上來源】
StockCharts 的原文已核實：「for a 100% accurate EMA, you should use every data point the stock has ever had」，以及「we calculate back at least 250 periods」。
    - 證據：
      - backend/app/strategy/builtin/macd_golden.py:55-57：required_warmup_bars 回傳 60
      - backend/app/strategy/engine.py:683：required = warmup+1 = 61；step1_bars.py 輸出為『macd_golden alone: 61；run_all set: 121』，最大值來自 lorentzian_knn
      - backend/app/repository.py:1184-1189：lookback_start = trading_dates[-(lookback_days+1)]，所以單獨執行實際載入 62 個交易日。重現腳本輸出每天 barsA=62、barsB=122
      - backend/app/backtest/matrix.py:3378-3382：_valid_ewm_kernel 以第一個有效值當 EMA 初值（窗首收盤價）
      - backend/app/api/screener.py:302 與 198-209：run_preset 會呼叫 _update_cache_strategy，且只在快取 as_of 相同時覆寫該策略結果
      - backend/app/services/screener.py:248-249、293-298：run_all 走慢路徑，讀 180 個日曆天後裁成最後 122 個交易日
      - 本地重算（AUDIT_SCRATCH/work/verify-mgrepro/step2_repro.py，輸出在 step2_log.txt 與 step2_out.json）：TOTALS A(62 根)=299、B(122 根)=291、C(全歷史)=292；A 與 C 對稱差 53，B 與 C 對稱差 1，A 與 B 對稱差 52。金叉判定本身 gA=1462、gC=1488，A 與 C 對稱差 492，B 與 C 對稱差 6
      - 9/23：A 為 [...,'9910.TWSE','00408A.TWSE']；B、C 為 [...,'2618.TWSE','00408A.TWSE']。B 與實際 strategy_cache.json 的 macd_golden 10 檔集合與順序完全相同（B==cache order True）
      - 9/18：C 與 B 選入自選股 3545.TWSE，A（單獨執行）漏掉（C-A 為 ['2404','2520','3545','7610']）
      - 少於 60 根的選股，A、B、C 三種窗都一樣是 5 筆：009824 51 根（9/4）、00990B.TPEX 49 根（9/17）、00409A 14 根（9/21）、7855 31 根（9/22）、00408A 51 根（9/23）。parquet 的第一根：00409A 為 2026-09-02，00408A 為 2026-07-15
      - strategy_cache.json（as_of 2026-09-23）：macd_golden 的 00408A.TWSE score=0.0、ma60=None、momentum_60d=None
      - frontend/src/lib/api.ts:2526：screenerRunPreset 只有定義；全前端 grep RunPreset、run_preset 沒有其他命中。Strategies.tsx:63-64、Dashboard.tsx:422-423 只呼叫 api.screenerCached()。後端 run_preset 的呼叫者只有 tests/test_screener_builtin_params.py:101 與 tests/backtest/test_factor_rank_research.py:91
      - backend/app/strategy/engine.py:52：DEFAULT_BASIC_FILTER 宣告 exclude_new_days: 30，但 grep 整個 backend/app 只有這一處，沒有任何程式讀取（矩陣路徑的 _build_basic_filter_mask_uncached 也沒有實作）
      - 官方比對（step3_official.py）：10 檔在 2026-08～09 共 380 列，本地 raw_close、close 與 TWSE STOCK_DAY 的 close_twd 不符 0 筆，輸入價格正確
      - 線上來源 https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential ：『Ideally, for a 100% accurate EMA, you should use every data point the stock has ever had』、『On our charts, we calculate back at least 250 periods』

### [P2 → P2] ETF 的 turnover_rate 為 null，bullish_alignment 的評分因此被整筆歸 0（自選 0050、00878 排在後段）（部分成立）

- **主張**：bullish_alignment 的評分含 turnover_rate（0.3）。build_matrix_score 只要任一評分欄位是 NaN，整筆分數就設為 0；台股 ETF 沒有周轉率（N1 的修正只給主檔類別為 stock 的標的），所以所有 ETF 不論動能高低都是 0 分。歷史日期的 turnover_rate 也被刻意設為 null。
- **正確的定義或數值**：缺值欄位應只排除該欄位的權重（重新正規化），或替 ETF 以受益權單位數計算周轉率；分數 0 不應代表『資料缺』。
- **app 實際行為**：ETF 一律 0 分、排在最後；回測時歷史日期所有標的的這個欄位都是 NaN，分數全部為 0，排序失去意義（回測部分為推測）。
- **對使用者的影響**：自選 5 檔 ETF 在均線多頭清單中看起來是最弱的，實際只是缺周轉率；以評分取前 N 檔的回測或組合選股結果不可信。
- **修法建議**：build_matrix_score 對缺值欄位逐檔重新正規化權重；或用 ETF 受益權單位數補算周轉率，並保留歷史股本以供回測。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/bullish_alignment.py:36
  - backend/app/backtest/matrix.py:3741（score[~universe | ~all_finite] = 0.0）
  - backend/app/taiwan/enriched_refresh.py:86-89（註解：非今日的台股 turnover_rate 為 null）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - strategy_cache 9/23 bullish_alignment：166 檔中 44 檔 ETF 的 turnover_rate 都是 None，score 都是 0.0；0050 排第 126、00878 排第 143
  - enriched 9/01、9/15 沒有 turnover_rate 欄；9/22 有欄但 1960 列非 ETF 全部為 null；只有 9/23 有值
  - 官方 t187ap47_L 提供 ETF units_issued_or_converted，可以計算 ETF 周轉率
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：bullish_alignment 的 scoring 含 turnover_rate（權重 0.3），且 META 宣告 asset_types 含 etf。build_matrix_score 只要任一評分欄位不是有限值，就把整格分數設為 0（matrix.py:3741）。台股的周轉率分母來自 t187ap03_L 上市公司股本，這份資料不含任何 ETF，所以 enriched 裡所有 ETF 的 turnover_rate 都是 null。因此 ETF 不論動能高低一律 0 分、排在所有個股之後：9/23 策略快取中 44 檔 ETF 排第 123–166，其中 00898 的 60 日動能 +25% 仍排第 148。依 MSCI 的缺值處理慣例（排除缺值變數、重新平均）逐列重新正規化後，0050 從 126 升到 105、00878 從 143 升到 94、0055 從 129 升到 40。

自選 ETF 是 4 檔，不是 5 檔，而且只有 0050、00878 出現在該清單。對這兩檔，排名偏差屬中度；被嚴重壓低的是高動能 ETF。非今日的台股 turnover_rate 是刻意設為 null。以純函式重現時，歷史進場格的分數全為 0。但實際回測載入路徑的資料集結構不含 turnover_rate，Taiwan 部署的 stock instruments 又是空的，比較可能直接拋出 'matrix turnover_rate requires float_shares' 錯誤，而不是全 0 排序（推測，未實跑）。同一根因也讓 low_volatility_leader（69 檔 ETF）和 pullback_to_support（13 檔 ETF）的 ETF 全部為 0 分。建議修法：build_matrix_score 對缺值欄位逐列重新正規化權重。若改用官方 t187ap47_L 的受益權單位數補算 ETF 周轉率，也可行，但主流台灣財經網站並不顯示 ETF 周轉率，沒有可對齊的市場慣例。
    - 理由：核心主張成立：用程式碼、本地資料和純函式重現都證實了。bullish_alignment 的 scoring 含 turnover_rate 0.3。build_matrix_score 裡 all_finite 要求每個評分欄位都是有限值，否則整格歸 0（matrix.py:3690、3706、3741），而且這是有測試固定的既有設計。台股 ETF 的 turnover_rate 全是 null，主因是股本來源 t187ap03_L（上市公司基本資料）只收公司：share_capital.parquet 有 1988 檔，其中 ETF 0 檔（主檔共 391 檔 ETF）。finding 把原因寫成「N1 的修正只給 stock」不精確。N1 的 _taiwan_stock_symbols 確實只補個股，但那是盤中即時K與自選頁的路徑。enriched 正本缺 ETF 周轉率，是因為股本檔本身沒有 ETF。

策略快取 2026-09-23 的 bullish_alignment 共 166 檔，其中 ETF 44 檔，turnover 全為 null、score 全為 0，排名集中在 123–166，全部排在最後一檔個股之後。實例：00898 的 60 日動能 +25.4%，排第 148；2382 的 60 日動能 -7.2%，得 3.24 分，排第 122。我用快取裡的動能與周轉率值重算個股分數，與儲存值最大差 1e-5，公式確認一致。

按 MSCI 的權威做法（缺值變數排除、其餘變數重新平均，不補 0）逐列重新正規化權重後：0050 從 126 升到 105，00878 從 143 升到 94，0055 從 129 升到 40，00898 從 148 升到 47。這證明 0 分造成實質排序錯誤。

META 的 asset_types 是 ["stock","etf"]，表示策略本來就打算納入 ETF 並給分，所以這是明確的功能錯誤。進場訊號與清單成員不受影響，只影響分數和排序，而 limit 500 大於 166 不會截斷。因此 P2 恰當，不到 P1。

需要修正的細節：
1. 「自選 5 檔 ETF」錯誤。自選 9 檔中 ETF 只有 4 檔（0050、00878、00885、00893），其中只有 0050、00878 出現在 9/23 的 bullish_alignment 結果裡。
2. 對 0050、00878 本身，重新正規化後仍在中後段（105、94／166），「看起來最弱、其實只是缺周轉率」的說法對這兩檔誇大了。真正被嚴重壓低的是高動能 ETF。
3. 回測部分：以純函式重現，9/23 以前 8,775 個進場格的分數全部為 0，finding 所述成立。但實際的回測載入路徑（load_market_data_matrix_from_parquet）推斷的資料集結構沒有 turnover_rate 欄位（第一個分區 2024-01-02 沒有這欄），而 Taiwan 部署的 stock instruments 是空的（data/instruments 為空目錄）。依程式碼推斷，比較可能直接拋出「matrix turnover_rate requires float_shares」錯誤，而不是全 0 排序（推測，未實跑回測）。

新面向：同一根因也影響 low_volatility_leader（ETF 69 檔全為 0 分）和 pullback_to_support（ETF 13 檔全為 0 分），這兩個策略同樣宣告 asset_types 含 etf。

線上來源：MSCI 方法論明文規定缺值變數的 z-score 要排除、用其餘變數平均，並說明不以 0 取代。另外，官方 t187ap47_L 確有 ETF 受益權單位數，可以算出 ETF 周轉率。但 HiStock、Yahoo 奇摩、MoneyDJ 的 0050 頁面都沒有顯示 ETF 周轉率，市場上沒有可對齊的慣例，所以修法以「缺值欄位重新正規化權重」較穩妥。
    - 證據：
      - backend/app/strategy/builtin/bullish_alignment.py:20 asset_types ["stock","etf"]；:36 scoring {momentum_60d:0.4, momentum_20d:0.3, turnover_rate:0.3}
      - backend/app/backtest/matrix.py:3690 all_finite = universe.copy()；:3706 all_finite[:, start:stop] &= finite；:3741 score[~universe | ~all_finite] = 0.0（tests/backtest/test_matrix_strategy.py:877 以相同式子固定此行為）
      - backend/app/strategy/engine.py 的 _run_matrix_strategy 與 matrix.py:3557 顯示選股與回測都走 build_matrix_score
      - backend/app/taiwan/share_capital_store.py:36 股本來源是 t187ap03_L／mopsfin_t187ap03_O；:11-13 註明非今日的台股分母一律設為 null
      - backend/app/taiwan/enriched_refresh.py:85-89 註解：compute_enriched 對非今日的台股列把 turnover_rate 設為 null
      - backend/app/services/quote_service.py:196 _fill_taiwan_turnover_rate 只補個股（N1 路徑，不是 enriched 缺值的主因）
      - 本地（check_data.py）：share_capital.parquet 1988 檔，security_master 中 ETF 391 檔，其中出現在 share_capital 的 = 0；enriched 9/01、9/15 沒有 turnover_rate 欄；9/22 有欄，但 2316 列非 null 數 = 0；9/23 非 null 1087 列，0050／00878／00885／00893 為 null，2454 = 0.400354（= 6,425,491 / 1,604,952,613）
      - 本地（check_cache.py，strategy_cache.json，as_of 2026-09-23）：bullish_alignment 共 166 列；ETF 44 列 turnover 全為 null、score 全為 0、最高名次 123；個股 122 列 score 無 0、最高 70.62；0050 排第 126、00878 排第 143；00898（mom60 0.2537）排第 148；2382（mom60 -0.072）score 3.24、排第 122
      - 本地（renorm.py）：用快取值重算個股分數，與儲存值最大差 1.0e-5；逐列重新正規化後 0050 從 126 升到 105、00878 從 143 升到 94、00898 從 148 升到 47、0055 從 129 升到 40
      - 本地（repro_score.py，純函式 build_market_data_matrix + compute_signals + build_matrix_score，資料為 2026-05 起的 enriched）：9/23 進場 231 檔，其中 ETF 70 檔，score>0 的 ETF = 0；9/23 以前進場格 8,775 個，score>0 的格數 = 0
      - 本地（schema.py）：pyarrow 對 kline_daily_enriched 推斷的資料集結構不含 turnover_rate（第一個分區 date=2024-01-02）；data\instruments 為空目錄；matrix.py:1434-1437 在沒有 float_shares 時會 raise ValueError('matrix turnover_rate requires float_shares')，所以實際回測比較可能報錯（推測，未實跑）
      - 同根因：strategy_cache 的 low_volatility_leader 有 ETF 69 檔，score 全為 0；pullback_to_support 有 ETF 13 檔，score 全為 0（兩者 asset_types 都含 etf，scoring 都含 turnover_rate 0.3）
      - 線上：MSCI Global Investable Market Value and Growth Index Methodology（2023 年 5 月）第 12 頁，https://www.msci.com/index/methodology/latest/GVG ——原文：'Only available variable z-scores are used and missing variable z-scores are excluded from the calculation.'，並舉例 E/P 缺值時 Value Z = 1/2 (Z_BV/P + Z_D/P)；成長分數一節另寫 'only five variables are averaged, rather than replacing the sales growth trend with a zero value.'
      - 官方參考資料 csv/twse_t187ap47_L_etf_meta.csv（report_date 2026-09-22）：0050 units_issued_or_converted = 22,051,000,000、00878 = 18,787,290,000，可算出 9/23 周轉率：0050 ≈ 58,059,253 / 22,051,000,000 = 0.263%，00878 ≈ 0.141%
      - 線上：https://www.moneydj.com/etf/x/basic/basic0004.xdjhtm?etfid=0050.tw 顯示成交量 '58,059,253'，與本地 enriched 9/23 的 0050 volume 相同；HiStock（https://histock.tw/stock/0050）與 Yahoo 奇摩股市（https://tw.stock.yahoo.com/quote/0050.TW）的 0050 頁面都沒有顯示周轉率；Goodinfo 回 403、Investopedia 無法抓取、zh.wikipedia 回 404
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：bullish_alignment 以 momentum_60d 0.4、momentum_20d 0.3、turnover_rate 0.3 評分（bullish_alignment.py:36），而 build_matrix_score 只要任一評分欄位非有限值，就把整列分數設為 0（matrix.py:3741）。台股 ETF 的周轉率分母來自 share_capital.parquet（t187ap03_L／mopsfin_t187ap03_O），這兩個端點不含 ETF；以受益權單位數當分母，又是 6h 決策文件刻意拒絕的做法（docs/taiwan-share-capital-phase-6h.md:104）。因此 ETF 的 turnover_rate 永遠是 null。這與 N1 無關，N1 的 stock 過濾只作用在盤中補值。

結果是 2026-09-23 均線多頭清單 166 檔中，44 檔 ETF 不論動能高低都顯示 0 分，被排在第 123–166 名：0050 第 126、00878 第 143。自選 4 檔 ETF 中只有這兩檔入選。清單上限 500 檔，ETF 沒有被截掉，只是順序與分數錯誤。

依 finding 建議的修法（逐列重新正規化，或用官方 t187ap47_L 單位數補算周轉率）重算：
- 0050 會在第 105 或 133 名，00878 在第 94 或 130 名。兩檔自選 ETF 仍在後段，主因是 ETF 動能本來就低。
- 受影響最明顯的是少數高動能 ETF，例如 0055，應在第 40–57 名。

回測方面，目前台股部署下，直接讀 parquet 的回測載入器因 dataset schema（取自 2024-01-02 分區）沒有 turnover_rate、data/instruments 又是空的，會丟出 ValueError『matrix turnover_rate requires float_shares』。回測因此直接失敗，不會以全 0 分照代號順序選股。這個限制在 6h 文件第 151–152 行已有記錄。
    - 理由：核心機制與選股快取的數字都能完全重現。bullish_alignment 的評分含 turnover_rate 0.3；build_matrix_score 會把任一評分欄位非有限值的列歸 0。我依相同規則，用快取 166 列的 momentum_60d、momentum_20d、turnover_rate 重算分數，與快取 score 的最大絕對差為 1.0e-5（float32 捨入）。44 檔 ETF 的 turnover_rate 全為 None，score 全為 0，排在第 123–166 名；0050 排第 126、00878 排第 143，與 finding 一字不差。enriched 各分區的欄位與非空值分布也重現。官方 t187ap47_L 確實涵蓋清單中全部 44 檔 ETF 的受益權單位數。

以下三點與 finding 不符：

(1) 回測敘述不成立。回測直接讀 parquet 的載入器用 pyarrow dataset，schema 取自第一個分區 date=2024-01-02，而該分區沒有 turnover_rate 欄，所以 turnover_rate 不算 parquet 欄位，改向 instruments 要 float_shares。純台股部署的 data/instruments 是空的，實跑 load_market_data_matrix_from_parquet（cache_root=None）直接丟出 ValueError('matrix turnover_rate requires float_shares')。回測因此回「回测矩阵准备失败」，不是「分數全 0、照代號順序選股」。「所有歷史列都 0 分」只在歷史 NaN 真的進到 build_matrix_score 時才會發生：我用純函式管線模擬，9/23 之前 36,343 個進場列沒有一列非 0。回測無法進行的限制，docs/taiwan-share-capital-phase-6h.md:151-152 已有記錄。

(2) 原因歸屬不精確。盤後選股快取讀的是 enriched_refresh 發布的表，分母來自 share_capital.parquet（t187ap03_L／mopsfin_t187ap03_O）。這份檔 1988 列中沒有任何 ETF，官方 t187ap03_L 也查不到 0050、00878、00885、00893。而且「以 ETF 受益權單位數當股本」是 6h 決策文件（docs/taiwan-share-capital-phase-6h.md:104）刻意拒絕的推論。N1 的 stock 過濾只作用在盤中的 _fill_taiwan_turnover_rate（quote_service.py:195），不是 9/23 快取缺值的原因。

(3) 使用者影響誇大。自選只有 4 檔 ETF，不是 5 檔；9/23 進入均線多頭清單的只有 0050、00878。limit 500 大於 166 檔，ETF 沒有被截掉，只是被排到最後、顯示 0 分。就算照 finding 的修法處理，兩檔自選 ETF 仍在後段：
- 逐列重新正規化：0050 排第 105、00878 排第 94。
- 用官方單位數補算周轉率：0050 排第 133、00878 排第 130。

主因是 ETF 動能本來就低：ETF 的 momentum_60d 中位數 0.035，個股為 0.134。真正被明顯壓錯位置的是少數高動能 ETF，例如 0055，修正後應在第 40 或第 57 名，目前卻不高於第 123 名。

結論：「缺值被當成 0 分、ETF 一律墊底」是明確的功能錯誤，維持 P2；回測部分與原因歸屬需要更正。
    - 證據：
      - 程式碼：backend/app/strategy/builtin/bullish_alignment.py:36 scoring={momentum_60d:0.4, momentum_20d:0.3, turnover_rate:0.3}；:42 EXECUTION_BACKEND='matrix_native'
      - 程式碼：backend/app/backtest/matrix.py:3741 `score[~universe | ~all_finite] = 0.0`；選股路徑 app/strategy/engine.py:1256 直接取 signals.score（matrix_native 策略經 MatrixStrategyPipeline → build_matrix_score，matrix.py:3557）
      - 重算（s3_rescore.py）：依 build_matrix_score 規則用快取 166 列重算，與快取 score 的最大絕對差為 1.0013987e-05
      - 快取（s1_cache.py，data/user_data/strategy_cache.json，as_of=2026-09-23，updated 2026-09-23 21:40:24 +08:00）：bullish_alignment 共 166 列，其中 ETF 44 列（turnover null 44、score=0 44），個股 122 列（turnover null 0，score 3.24–70.62）；第一個 0 分出現在第 123 名；0050 第 126、00878 第 143，2454 為 18.79 分、第 36 名
      - enriched（s2_enriched.py）：第一個含 turnover_rate 欄的分區是 date=2026-09-16；9/01、9/15 沒有此欄；9/18、9/21 非空 0 列；9/22 個股 1960 列、ETF 356 列，非空都是 0；9/23 個股 1087/1087 非空、ETF 0/236。另注意：9/23 分區在 22:07:42 被重寫，只剩 1323 列，晚於快取計算時點
      - 股本來源：data/taiwan/share_capital.parquet 共 1988 列（stock 1986、主檔查無 2、ETF 0）。官方 reference csv/twse_t187ap03_L_share_capital.csv 不含 0050、00878、00885、00893（0/4）。決策文件 docs/taiwan-share-capital-phase-6h.md:104 將『ETF issued or beneficiary units as company share capital』列為拒絕的推論
      - N1 過濾只在盤中路徑：app/services/quote_service.py:195 `_taiwan_stock_symbols` 只補 instrument_type=='stock'；盤後 enriched 的分母為 app/taiwan/enriched_refresh.py:86-95 → share_capital_store.as_instruments；歷史列置 null 的程式在 app/share_capital.py:39-51、58
      - 官方 ETF 單位數（csv/twse_t187ap47_L_etf_meta.csv，report_date 2026-09-22）：清單中 44 檔 ETF 全部有值。9/23 以 STOCK_DAY 成交股數／單位數計算：0050＝58,059,253/22,051,000,000＝0.2633%；00878＝26,437,407/18,787,290,000＝0.1407%；00885＝0.1940%；00893＝0.2337%
      - 線上交叉核對單位數：MoneyDJ https://www.moneydj.com/ETF/X/Basic/Basic0004.xdjhtm?etfid=0050.TW 『ETF規模：2,487,982.00(百萬台幣)(2026/09/23)』，除以 22,051,000,000 單位得 112.83 元／單位，9/23 收盤 112.45；00878 頁『659,351.30(百萬台幣)(2026/09/23)』，除以 18,787,290,000 得 35.096，收盤 35.09。單位數與規模、價格一致。WebSearch 額度已用完，Investopedia 與 Wikipedia 周轉率定義頁都抓不到（前者拒絕抓取、後者 404）
      - 替代排名（s3_rescore.py）：逐列重新正規化時 0050 第 105、00878 第 94，ETF 最佳第 40（0055），前 50 名有 2 檔 ETF；用官方單位數補 ETF 周轉率時 0050 第 133、00878 第 130，ETF 最佳第 57，前 50 名 0 檔。momentum_60d 中位數：個股 0.1342、ETF 0.0347；momentum_20d 中位數：個股 0.1422、ETF 0.0677
      - 回測實跑載入器（s5_backtest_loader.py）：load_market_data_matrix_from_parquet(kline_daily_enriched, 2026-08-01..2026-09-23, field_columns={close,volume,turnover_rate}, instruments=空, cache_root=None) → ValueError『matrix turnover_rate requires float_shares』（app/backtest/matrix.py:1437）。原因：pyarrow dataset schema 取自 date=2024-01-02，`'turnover_rate' in ds.schema.names` 為 False；data/instruments 是空目錄。呼叫鏈：app/backtest/strategy.py:250-256（scoring 依賴併入 base_columns）→ app/backtest/engine.py:510-515 → strategy.py:1151 回傳『回测矩阵准备失败』
      - 純函式管線模擬（s4_pipeline.py，160 個 enriched 分區，自組 DataFrame → build_market_data_matrix → MatrixStrategyPipeline）：9/23 個股進場 161 檔全部非 0 分、ETF 70 檔全部 0 分；9/23 之前各日共 36,343 個進場列，非 0 分為 0 列（此為『歷史 NaN 若進入評分』的條件情境）
      - 範圍外觀察（未深究）：快取的 volume 與官方 STOCK_DAY 9/23 成交股數不同，2330 為 21,978,873 對 22,817,873，2454 為 6,425,491 對 6,430,491
      - 操作紀錄：腳本都在 scratchpad\audit\work\verify-etf-turnover\；repo 工作樹前後快照比對無變化（worktree-snapshot exit 0）；data\ 下只有執行中的後端寫 backend.log。第一次 import app 的 s4 沒設 PYTHONDONTWRITEBYTECODE，可能在 backend\app 產生 __pycache__，進而觸發執行中後端的 watchfiles 重載（推測，log 同期出現『changes detected』，但也有其他來源）

### [P3 → P3] DEFAULT_BASIC_FILTER 的 exclude_new_days=30 從未實作（部分成立）

- **主張**：引擎的預設基本過濾宣告要排除上市 30 天內的新股，但 _basic_filter_expr 與 matrix 的 _build_basic_filter_mask_uncached 都沒有處理這個鍵，全 repo 只有宣告這一處。
- **正確的定義或數值**：宣告的過濾要生效，或從預設值中刪除，避免誤導。
- **app 實際行為**：設定形同虛設；新掛牌（台股新股上市前 5 日無漲跌幅限制）仍會被 macd_golden 等策略選入。
- **對使用者的影響**：低；新上市 ETF 或個股偶爾以極短歷史入選。
- **修法建議**：在 matrix 基本過濾中以每檔有效 K 棒數（valid bar 計數）實作 exclude_new_days，或移除這個鍵。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/engine.py:52
  - backend/app/strategy/engine.py:1416-1480
  - backend/app/backtest/matrix.py:3641-3673
- **線上證據**：
  - （無）
- **本地計算證據**：
  - grep exclude_new_days：backend/app 只有 engine.py:52 一處，frontend 與 tests 都沒有
  - macd_golden 在 9/21 選入上市僅 14 根 K 棒的 00409A（indep_results 的 nbars_window）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：引擎的 DEFAULT_BASIC_FILTER（engine.py:52）宣告 exclude_new_days=30，但 _basic_filter_expr（engine.py:1416-1480）和 matrix 的 _build_basic_filter_mask_uncached（matrix.py:3641-3673）都沒有實作這個鍵，這個設定形同虛設。在 .py 程式碼中只有宣告這一處。不過 backend/app/strategy/prompts/ 還有 4 處，其中 ai_generator.py 載入的 strategy-guide-compact.md 會讓 AI 產生的策略也寫入 exclude_new_days，同樣不生效；strategy-guide.md 更宣稱這組參數由「引擎统一处理」。重現結果：macd_golden 在預設設定下，於 2026-09-21 選入掛牌才 14 根 K 棒的 00409A.TWSE（2026-09-02 掛牌）；2025-12-01～2026-09-23 間共 4623 檔次入選，其中 31 檔次在首根 K 棒後 30 個交易日以內，21 檔次距掛牌未滿 30 個日曆日。00409A 屬國外成分證券 ETF，本來就沒有漲跌幅限制，與「新股前 5 日」規則無關。
    - 理由：核心主張完全重現：DEFAULT_BASIC_FILTER 宣告了 exclude_new_days=30，但選股的 polars 路徑（_basic_filter_expr）和 matrix 路徑（_build_basic_filter_mask_uncached）都沒有處理這個鍵。我用本地 enriched parquet 加上 app 的純計算函式（build_market_data_matrix、MatrixStrategyPipeline、macd_golden.MATRIX_STRATEGY、DEFAULT_BASIC_FILTER）重跑，9/21 確實選入 00409A.TWSE，當時累計只有 14 根 K 棒，和 finding 的 14 一致。重現的保真度已確認：同一支腳本算出的 9/23 入選清單有 10 檔，和 app 寫入的 data/user_data/strategy_cache.json 的 macd_golden 結果（as_of 2026-09-23，total=10）完全相同，兩邊差集都是空的。strategy_overrides 目錄是空的，所以使用者實際跑的就是預設過濾。

有一點需要更正：finding 說「grep exclude_new_days：backend/app 只有 engine.py:52 一處」「全 repo 只有宣告這一處」，這不正確。backend/app/strategy/prompts/ 底下另有 4 處，其中 strategy-guide-compact.md 被 ai_generator.py:20 載入為 AI 產生策略的提示詞，會叫 LLM 在 basic_filter 寫 exclude_new_days: 30；strategy-guide.md:27 更明寫「基础过滤参数 (Stage 1, 引擎统一处理)」。所以 AI 產生的策略也會宣告這個過濾，但同樣不會生效。這比原 finding 的影響面稍大。在可執行程式碼（.py）裡，確實只有 engine.py:52 一處；frontend 與 tests 都沒有出現（git grep 確認）。

finding 的「actual」欄提到「台股新股上市前 5 日無漲跌幅限制」，這次沒能上網核實：WebSearch 額度已用完，Wikipedia 的漲跌幅限制頁談的是中國 A 股。另外，00409A 是國外成分證券 ETF，依 Wikipedia 臺灣證券交易所條目「含海外股票的ETF不受此限」，它本來就沒有漲跌幅限制，與是否為新掛牌無關。這句只是旁述，不影響核心結論。

嚴重度維持 P3。在 2025-12-01～2026-09-23 共 198 個交易日的視窗內，macd_golden 預設設定共入選 4623 檔次：其中 31 檔次在首根 K 棒後 30 個交易日以內（約 0.67%），21 檔次距 security_master 的 listing_date 未滿 30 個日曆日。都只是偶發的短歷史入選，不構成資料破壞或錯誤的金融口徑。
    - 證據：
      - 程式碼：backend/app/strategy/engine.py:52 宣告 "exclude_new_days": 30；engine.py:1416-1480 的 _basic_filter_expr 只處理 price/market_cap/float_cap/amount/turnover/exclude_st/boards，沒有 exclude_new_days
      - 程式碼：backend/app/backtest/matrix.py:3641-3673 的 _build_basic_filter_mask_uncached 同樣沒有處理 exclude_new_days；engine.py:1184-1200 的 _run_matrix_strategy 把 strategy.basic_filter 原樣傳給 MatrixPipelineConfig
      - 程式碼：engine.py:468-475 把 DEFAULT_BASIC_FILTER 合併進每支策略的 basic_filter；macd_golden.py 沒有 BASIC_FILTER，META 也沒有 basic_filter，所以直接用預設值
      - git grep -n exclude_new_days：engine.py:52，以及 prompts/strategy-example.md:37、strategy-guide-compact.md:36、strategy-guide.md:34、strategy-guide.md:309；frontend/src 與 tests 都是 0 處。ai_generator.py:20 的 GUIDE_PATH 指向 strategy-guide-compact.md；strategy-guide.md:27 註明「基础过滤参数 (Stage 1, 引擎统一处理)」
      - 本地計算：uv run python scratchpad\audit\work\verify-newdays\bars.py 00409A 的結果：taiwan/daily 與 kline_daily_enriched 都是 16 列，首日 2026-09-02；security_master 的 listing_date=1150902（即 2026-09-02）；9/21 的 vol_ratio_5d=1.849776，close=9.89，amount=2.689e8
      - 本地計算：repro.py（視窗 2025-12-01～2026-09-23，198 日 × 2345 檔，用 DEFAULT_BASIC_FILTER 與預設參數）的輸出：00409A 在 2026-09-21 cum_bars=14、entry=1；9/21 共 41 檔入選，00409A 是其中歷史最短的
      - 本地計算，數值並列：finding 寫 00409A 在 9/21 有 14 根 K 棒，重現結果也是 14 根（日曆日齡 19 天）
      - 保真度：fidelity.py 算出 repro 的 09-23 入選 10 檔，與 data\user_data\strategy_cache.json 的 macd_golden（as_of 2026-09-23，total 10）集合完全相同，cache−repro=[]，repro−cache=[]
      - 獨立 numpy MACD（adjust=False）計算 00409A：09-18 DIF=-0.026830 ≤ DEA=-0.010997；09-21 DIF=-0.006322 > DEA=-0.010062，確實在 9/21 金叉
      - 影響量化：視窗內 macd_golden 共 4623 檔次入選，其中 31 檔次距首根 K 棒 ≤30 個交易日（例：7711.TWSE 在 2025-12-02 第 10 根、6907.TPEX 在 2026-02-10 第 9 根、6947.TWSE 在 2026-08-05 第 10 根）；21 檔次距 listing_date 未滿 30 個日曆日
      - 線上：https://zh.wikipedia.org/zh-tw/臺灣證券交易所 原文「新臺幣ETF...原則上設有10%的漲跌幅限制，但含海外股票的ETF不受此限」。00409A 的 raw_category 是「國外成分證券主動式交易所交易基金(股票)」，所以它的無漲跌幅限制與新掛牌無關。至於「新股上市前 5 日無漲跌幅限制」，因 WebSearch 額度用盡未能核實（推測為正確的一般規則）
      - 使用者設定：data\user_data\strategy_overrides 是空目錄，沒有任何策略覆寫過 basic_filter
      - 唯讀確認：稽核前後 git status --short 相同，只有既有的 3 個修改檔

### [P3 → P3] 布林帶使用樣本標準差（ddof=1），與 Bollinger 原作者的母體標準差不同（部分成立）

- **主張**：策略矩陣與 enriched 的 boll_upper/lower 都用 n−1 的樣本標準差，帶寬比原定義寬 sqrt(20/19)=2.6%，突破門檻因此偏高。
- **正確的定義或數值**：依原作者定義使用母體標準差（除以 n）。
- **app 實際行為**：使用樣本標準差，9/23 少選 2 檔。
- **對使用者的影響**：與券商、TradingView 等以母體標準差繪製的布林帶有細微落差，邊界個股不一致。
- **修法建議**：matrix.py:3895 改用 ddof=0，pipeline.py:417 改為 rolling_std(20, ddof=0)，兩處同步修改。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3895（valid_rolling_std(..., 20, ddof=1)）
  - backend/app/indicators/pipeline.py:417（polars rolling_std 預設 ddof=1）
- **線上證據**：
  - bollingerbands.com：『(We use the population calculation for standard deviation). The defaults … 20 periods … plus and minus two standard deviations』 https://www.bollingerbands.com/bollinger-bands
- **本地計算證據**：
  - compare_cache.py：改用母體標準差時，9/23 boll_breakout 由 48 檔增為 50 檔（多出 00897、6205）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：策略矩陣（matrix.py:3895，ddof=1）與收盤後落檔的 enriched（pipeline.py:417 compute_indicators，polars rolling_std 預設 ddof=1）都用 n−1 的樣本標準差，與 Bollinger 原作者定義的母體標準差（除以 n，bollingerbands.com、Wikipedia 皆明載）不同。σ 比值恆為 sqrt(20/19)=1.02598，半帶寬（2σ）寬 2.6%，上軌高出約 0.052σ。boll_breakout 在 9/23 因此少選 00897、6205（48 對 50，已用本地 parquet 重現，並與 strategy_cache.json 逐一核對）；近 50 個交易日母體口徑會多出 6.7% 命中，43/50 天結果不同。

另有原 finding 沒提到的面向：即時增量路徑 compute_enriched_today（pipeline.py:1524-1531）用母體標準差，突破條件用 >=（pipeline.py:1669）；收盤後路徑用樣本標準差與 >（pipeline.py:657）。開著即時行情時，K 線圖當日即時 K 棒（kline.py:577-584）與歷史 K 棒的布林帶口徑不一致，收盤重算後當日帶寬會跳寬 2.6%。

修正時 matrix.py:3895 與 pipeline.py:417 要同步改成 ddof=0（test_matrix_strategy.py 的 parity 測試把兩者鎖在一起），突破條件的 >/>= 也應統一。「TradingView 以母體標準差繪製」無法從其公開文件證實，屬於推測。
    - 理由：finding 的數值宣稱全部重現了，但對 enriched 的描述不完整。

重現結果：
(1) matrix.py:3895 呼叫 valid_rolling_std(..., 20, ddof=1)，核心在 matrix.py:3086 用分母 window-ddof=19；pipeline.py:417 用 polars 1.40.1 的 rolling_std(20)，簽名預設 ddof=1。
(2) 9/23 已落檔的 enriched 共 1320 列有布林值。boll_upper 與 ddof=1 重算值的最大誤差是 3.1e-10，與 ddof=0 最多差 102.9，全數吻合樣本標準差。σ(ddof1)/σ(ddof0) 在所有列都等於 1.0259783520851542，就是 sqrt(20/19)。
(3) 用本地 enriched 90 個交易日建 MarketDataMatrix，直接跑 boll_breakout.MATRIX_STRATEGY.compute_signals（預設參數），ddof=1 得 56 檔。其中 8 檔成交額低於 DEFAULT_BASIC_FILTER 的 amount_min 2000 萬，扣掉後剩 48 檔，與 strategy_cache.json 的 48 檔逐一相同。改成 ddof=0 後剩 50 檔，多出的正是 00897、6205，沒有任何一檔被移除。兩檔的 9/23 收盤價與官方 MI_INDEX 相符（13.78、72.0），也都通過價格與成交額門檻。
(4) 線上來源：bollingerbands.com 與 Wikipedia 都明寫使用母體標準差。

需要修正的地方：
(a) 「enriched 都用 n−1」只適用於收盤後由 compute_indicators 重算、落檔的歷史 enriched。即時增量路徑 compute_enriched_today（pipeline.py:1524-1531，部分和來自 repository.py:910-911）是 Σx²/20−mean²，也就是母體標準差，而且突破條件用 >=（pipeline.py:1669），compute_indicators 則用 >（pipeline.py:657）。
使用者開著即時行情時，K 線圖當日即時 K 棒的 boll_upper/lower 取自這條路徑（kline.py:547、577-584），和前面各根歷史 K 棒的標準差口徑不同；收盤後重算時，當日 K 棒的布林帶會外擴 2.6%。以 9/23 為例，不加量能條件時，即時路徑的突破訊號是 118 檔，落檔 enriched 是 112 檔。原 finding 沒提到這個同一系統內的口徑分歧。
(b) 「TradingView 以母體標準差繪製」查無證據。TradingView 的 Bollinger 說明頁沒寫母體或樣本；Pine 手冊是動態頁面，抓不到內容；搜尋額度也已用完。這點標為推測。
(c) 「帶寬寬 2.6%」是指半帶寬（2σ）；上軌本身只高出 2(σ1−σ0)≈0.052σ0，不是價格的 2.6%。

補充：matrix 與 pipeline 的 ddof=1 由 tests/backtest/test_matrix_strategy.py:57-69 的 parity 測試鎖在一起，修正時兩處必須同步，這點與 finding 的建議一致；改完後三條路徑會一致。另外 50 個交易日的量化：ddof=0 會多出 121 次命中（+6.7%），43/50 天結果不同；ddof=0 從未減少命中。

嚴重度：屬於定義偏差加上輕微的內部不一致，影響只落在邊界個股與圖表最後一根 K 棒，維持 P3。
    - 證據：
      - backend/app/backtest/matrix.py:3895 deviation = valid_rolling_std(market.close, close_valid, 20, ddof=1)；matrix.py:3086 denominator = float(window - ddof)，3130 np.sqrt(squared / denominator)
      - backend/app/indicators/pipeline.py:417 pl.col("close").rolling_std(20)；polars 1.40.1 簽名：rolling_std(..., ddof: int = 1)（指令：uv run python -c 'inspect.signature(pl.Expr.rolling_std)'）
      - backend/app/indicators/pipeline.py:1524-1531（compute_enriched_today）：boll_var = boll_sq_sum / 20 - boll_ma ** 2，為母體標準差；部分和來自 backend/app/repository.py:910-911 close.tail(19)
      - backend/app/indicators/pipeline.py:1669 即時路徑突破用 close >= boll_upper；pipeline.py:657 收盤後路徑用 close > boll_upper
      - backend/app/api/kline.py:547,577-584 即時 K 棒的 boll_upper/boll_lower 取自即時 enriched 列（母體標準差），歷史 K 棒取自 compute_indicators（樣本標準差）
      - backend/tests/backtest/test_matrix_strategy.py:57-69 以 assert_allclose 鎖定 matrix 與 compute_indicators 的 boll_upper/lower 一致（ddof=1 是刻意對齊），所以兩處必須同步修改
      - 本地重現（腳本 AUDIT_SCRATCH/work/verify-bollstd-repro/repro.py，輸出 repro_out.txt）：matrix ddof=1 未過濾 56 檔，只在重現結果出現的 8 檔（00851、00903、009801、1235、1338、1418、2321、4164）成交額都低於 2e7，扣除後 48 檔，與 strategy_cache.json 的 as_of=2026-09-23、total=48 逐一相同；ddof=0 新增 ['00897.TWSE','6205.TWSE']，移除 []
      - 並列比較：finding 寫 48→50（多 00897、6205），重現結果 48→50（多 00897、6205），完全一致。00897：close 13.78，upper(ddof1) 13.7821，upper(ddof0) 13.7661，vr5 1.664；6205：close 72.0，upper(ddof1) 72.0541，upper(ddof0) 71.8850，vr5 4.791
      - 9/23 enriched 1320 列：max|stored−u(ddof1)|=3.12e-10，max|stored−u(ddof0)|=102.94；σ1/σ0 最小值與最大值都是 1.0259783520851542，等於 sqrt(20/19)；不加量能條件的突破訊號：落檔 112 檔，ddof0 用 > 為 118 檔，用 >= 也是 118 檔
      - 官方資料交叉核對（repro2.py，輸出 repro2_out.txt）：以 reference\csv\twse_stock_day_202608_202609.csv 的官方收盤價重算 10 檔的 9/23 上軌，stored boll_upper 與 ddof1 的差都 ≤8.3e-11（例如 2379 為 760.5234，ddof0 為 759.5492；2330 為 2503.2539 對 2501.3801）；自選股 9/23 的突破判定在兩種口徑下都沒有翻轉。官方 MI_INDEX 9/23 收盤價：00897 為 13.78、6205 為 72.0，與本地相同
      - 50 個交易日量化（repro3.py，2026-07-16..2026-09-23，套用價格 ≥3 與成交額 ≥2e7）：ddof1 命中 1800 次，ddof0 命中 1921 次（+121，+6.7%），43/50 天不同；ddof1 有而 ddof0 沒有的命中為 0
      - 線上來源 https://www.bollingerbands.com/bollinger-bands 原文："We use the population calculation for standard deviation"；"20 periods for the moving average with the bands set at plus and minus two standard deviations"
      - 線上來源 https://en.wikipedia.org/wiki/Bollinger_Bands 原文："Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1."
      - 未能證實：https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/ 只寫 "20 Day Simple Moving Average + (Standard Deviation x 2)"，沒寫母體或樣本；Pine 手冊 https://www.tradingview.com/pine-script-reference/v6/#fun_ta.stdev 是動態頁面，抓不到內容；StockCharts 頁面也沒寫。finding 所說「TradingView 以母體標準差繪製」屬於推測
      - 工作樹核對：執行前後 git status --porcelain 的 md5 都是 ff5204bd3a2c3a3d1dcf21376bd4df03；numba 快取導向 scratchpad；沒有寫入 repo 或 data

### [P3 → P3] trend_breakout 的『60日新高』實際是『收盤價不低於近 60 根收盤（含當日）』，欄位標示為『60日最高价』；MA60 條件是多餘的（部分成立）

- **主張**：high_60d=收盤價 60 根滾動最大值（含當日），close>=high_60d 等於『高於前 59 根收盤』，不是以最高價計算的 N 日高點；pipeline 把欄位描述寫成『60日最高价』。只要 close≥60 根收盤最大值，就必然 close≥MA60，所以 require_above_ma60 永遠是多餘的。
- **正確的定義或數值**：文件或標籤明確寫出『收盤價 60 日新高』，或改用最高價口徑；移除或說明多餘的 MA60 開關。
- **app 實際行為**：畫面顯示『60日最高价』，其實是最高收盤價；有 5/12 檔在盤中高點口徑下並未創新高。
- **對使用者的影響**：使用者以為是價格創 60 日新高，實際門檻較寬鬆；切換『要求收盤價在MA60上方』不會有任何效果。
- **修法建議**：把標籤改為『60日最高收盤』，或新增以 high 計算、排除當日的 donchian_high_60 欄位給 trend_breakout 使用；移除 require_above_ma60 或改成 MA60 上彎。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3908-3909
  - backend/app/indicators/pipeline.py:162（"high_60d": "60日最高价"）
  - backend/app/indicators/pipeline.py:437-438
  - backend/app/strategy/builtin/trend_breakout.py:66-70
- **線上證據**：
  - Donchian 通道：『The Upper Band is the highest high of the last n periods』（https://www.avatrade.com/education/technical-analysis-indicators-strategies/donchian-channel-trading-strategies 等搜尋結果）
- **本地計算證據**：
  - nday_high.py：9/23 的 12 檔 trend_breakout 若改用『收盤 > 前 60 根最高價』，只剩 7 檔（1101 收 25.65 < 前高 26.10、6456、2444、2104、00402A 不成立）
  - indep_results：15 天內 726 列 close≥high60，其中 close≤ma60 為 0 列
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：trend_breakout 的批次與矩陣路徑中，high_60d 是含當日在內 60 根收盤的最大值，所以條件 close>=high_60d 等於「收盤不低於前 59 根收盤的最大值」，也就是收盤價創 60 日新高，不是用最高價算的 Donchian 高點。9/23 的 12 檔選股中，只有 7 檔的收盤高於前 60 根最高價；用 >= 算則有 8 檔。不成立的 5 檔是 00402A、1101（25.65 對前高 26.10）、2104（11.65 等於前高 11.65）、2444、6456。欄位標籤『60日最高价』（pipeline.py:162）出現在自訂訊號與監控規則的欄位選單，容易讓人誤以為是用最高價計算。策略卡顯示的是『60日新高』，前端訊號說明寫『收盤價創近 60 日新高』，這兩處是正確的。require_above_ma60 實際上多餘：661 日全市場資料中，開關開與關的 entry 差異為 0 格。只有 60 根收盤完全相同的退化情況，這個開關才會排除訊號。另外，即時增量路徑 compute_enriched_today（pipeline.py:1589-1597、repository.py:913）用 max(前 59 根最高價, 當日最高價) 計算 high_60d，和批次路徑的收盤口徑不一致。因此盤中的 signal_n_day_high 與 high_60d 欄位，實際上用的是另一種口徑。
    - 理由：finding 的數字全部重現了。細節有 4 處要修正，另外補 1 個原 finding 沒提到的面向：
(1) 用 App 自己的純計算函式 trend_breakout.MATRIX_STRATEGY.compute_signals，加上基本過濾（close≥3、amount≥2e7），算出 9/23 共 12 檔，和 strategy_cache.json 的 12 檔完全相同。以「收盤 > 前 60 根最高價」檢驗，只有 7 檔通過；不通過的 5 檔正是 00402A、1101、2104、2444、6456，與 finding 一致。1101 收 25.65，前高 26.10（2026-09-01），也一致。這 12 檔 9/23 的收盤價與最高價，都和官方 MI_INDEX 參考檔逐一相符。5 檔前高的值與日期，用 Yahoo 日K 核對：1101、2104、2444、6456 在同一日期有同樣的最高價；Yahoo 缺 00402A 9/22、9/23 兩天，所以它只核對到前高 10.09@7/01，當日值改用官方 MI_INDEX 核對。9/03～9/23 經基本過濾後，close≥high_60d 有 726 列，其中 close≤ma60 為 0 列，也重現（不加過濾則是 1041 列、0 列）。
(2) require_above_ma60 多餘：在完整 661 日、2346 檔的矩陣上，App 分別以開與關跑一次，entry 都是 22329 格，差異 0 格。不過「永遠」要加但書。60 根收盤完全相同的退化情況下，close=max=mean，所以 close>ma60 不成立，這時開關會排除訊號（合成序列已實測）。實際資料中從未出現這種情況。
(3) 用字修正：條件是「不低於」前 59 根收盤最大值（>=），不是「高於」。2104 的收盤 11.65 等於前高 11.65，所以「盤中高點口徑未創新高」的檔數，用嚴格 > 算是 5/12，用 >= 算是 4/12。
(4) 標籤部分成立。『60日最高价』來自 pipeline.py:162 的 ENRICHED_COLUMNS，只經由 /api/signals/options 與 monitor_rules 出現在自訂訊號與監控規則的欄位選單。trend_breakout 本身顯示的是『60日新高』。前端的訊號說明寫『收盤價創近 60 日新高』，factor.py 寫『60日最高收盘价』，這兩處其實是正確的。收盤價新高本來就是常見的選股口徑之一，所以問題主要在標籤與多餘參數，P3 合理。
(5) 新面向，原 finding 沒提到：『high_60d 以收盤價計算』只對批次、落檔與矩陣路徑成立。即時增量路徑 compute_enriched_today 用的是最高價，high_60d=max(前 59 根最高價, 當日最高價)。所以盤中的 signal_n_day_high 等於要求收盤等於當日最高價。兩條路徑的口徑不一致。落檔資料我查了 91 個交易日，全都是收盤口徑，可見盤中值沒有保留下來。對盤中畫面的影響屬推測，我沒有實際觀察到。
    - 證據：
      - 程式碼：backend/app/backtest/matrix.py:3908-3909 `valid_rolling_max(market.close, close_valid, 60)`；backend/app/indicators/pipeline.py:437-438 `pl.col("close").rolling_max(60)`；pipeline.py:162 `"high_60d": "60日最高价"`；backend/app/strategy/builtin/trend_breakout.py:67-71（close > ma60、close >= high_60d）
      - 標籤出現在哪裡：backend/app/api/signals.py:64-88 與 backend/app/api/monitor_rules.py:144-156 用 ENRICHED_COLUMNS 當欄位選單標籤；frontend/src/lib/signals.ts:104-108 的說明是『收盤價創近 60 日新高』；backend/app/backtest/factor.py:85 寫『60日最高收盘价』
      - 新面向：backend/app/indicators/pipeline.py:1589-1597 的即時路徑用 `max_horizontal(_high_59d, high)`；backend/app/repository.py:913 `pl.col("high").tail(59).max().alias("_high_59d")`；呼叫端 backend/app/services/quote_service.py:1681
      - 指令 `uv run python AUDIT_SCRATCH/work/verify-tbhigh/app_toggle.py` 輸出：`full history entries: ma60 on 22329 off 22329 diff cells 0`；`9/23 app compute_signals+basic filter: 12 [...] equals strategy_cache rows: True`；`matrix: close>=high_60d cells 74453 of which not close>ma60: 0`；`synthetic flat 10.0: ... close>=high_60d True close>ma60 False`
      - repro.py 輸出（9/23，12 檔）：app_rule 12；close>prior60 最高價（還原價）7；close>=prior59 最高價 8；close>prior60 最高價（原始價）7；不通過：['00402A.TWSE','1101.TWSE','2104.TWSE','2444.TWSE','6456.TWSE']；1101 close 25.65、前 60 根最高價 26.1；12 檔的 off_close/off_high（官方 twse_mi_index_20260923_quotes.csv）與本地 close/high 全部相等；9/23 的 stored high_60d 等於收盤 60 根最大值，1309/1309 檔
      - 並列比較：finding『7 檔通過、5 檔不成立』→ 本地重算 7／5，名單相同；finding『726 列、close≤ma60 為 0』→ 本地 726／0（加基本過濾，9/03～9/23），不過濾則 1041／0；repro.py 用 150 個交易日自算：close≥max60 共 10250 列、close≤ma60 為 0 列、以 Donchian 口徑不成立 4794/10129
      - stored_def.py：91 個交易日的落檔 high_60d 都是收盤口徑，不符合收盤口徑的日期 0 天（`dates where close_based < n: 0 of 91`）
      - Yahoo 日K（query1.finance.yahoo.com/v8/finance/chart/{code}.TW，存於 verify-tbhigh\yahoo\）：1101 前 60 根最高價 26.10@2026-09-01、2104 11.65@2026-07-06、2444 15.15@2026-09-22、6456 79.00@2026-09-22，與本地相同；00402A 在 Yahoo 只到 9/21，前高 10.09@2026-07-01 與本地相同
      - 線上定義：https://en.wikipedia.org/wiki/Donchian_channel 『Upper = HighestHigh(N)』；https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/ 『Upper Channel = 20 Day High』，並說明用的是期間最高價，不是最高收盤價

### [P3 → P3] consolidation_breakout 的評分與參數與『低檔量縮整理』的前提相反或過於寬鬆（部分成立）

- **主張**：評分以 distance_from_low_60d（預設方向 high）占 0.3，離 60 日低點越遠分數越高；in_bottom_zone 容許比 60 日最低收盤高出 45%；『量縮』門檻 1.05 反而容許量能增加 5%；20 日區間振幅上限 30% 也遠比常見平台整理寬鬆。
- **正確的定義或數值**：『低檔』策略應偏好接近低點的標的（direction low），並要求整理期量能確實下降（例如 ≤0.8～0.9 倍）；突破量倍數可參考 1.4～1.5 倍。
- **app 實際行為**：評分偏好已遠離低點的股票，量縮條件實際上是『量沒有明顯放大』。
- **對使用者的影響**：名稱與排序互相矛盾，使用者依排序會先看到最不『低檔』的標的。
- **修法建議**：預設 scoring_directions 將 distance_from_low_60d 設為 low，或改用其他因子；把 volume_contraction_max 預設值降到 1.0 以下，並在 META 描述中寫明實際門檻。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/consolidation_breakout.py:233（scoring distance_from_low_60d 0.3）
  - backend/app/strategy/builtin/consolidation_breakout.py:188-204（bottom_zone_max 0.45、range_max 0.30）
  - backend/app/strategy/builtin/consolidation_breakout.py:215-222（volume_contraction_max 1.05）
  - backend/app/strategy/builtin/consolidation_breakout.py:267-271
- **線上證據**：
  - O'Neil／CAN SLIM：突破量應高於均量 40–50%，整理期量能應遞減（https://pro.stockalarm.io/blog/canslim-growth-stocks-system 等搜尋結果）
- **本地計算證據**：
  - score_check.py：9/23 的 19 檔中，排第 2 的 2241 距 60 日低點 +43.3%（最接近上限 45%），排第 1 的 2104 為 +22.1%；距低點最近的（約 +13%）排在最後
  - docs/radar-strategies-validation.md 的回測顯示本策略全市場 PF 0.86，可作為參考
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：consolidation_breakout 的設定和它標榜的「低檔量縮整理」不一致，具體有以下幾點：

1. 評分方向：consolidation_breakout.py:73 給 distance_from_low_60d 權重 0.3。沒有 override 時方向預設為 high（scoring.py:82-90、matrix.py:3736），所以越遠離 60 日最低收盤分數越高。
2. 低檔門檻：consolidation_breakout.py:28-31、96-100 允許收盤比 60 日最低收盤（含當日）高出 45%。
3. 量縮門檻：consolidation_breakout.py:55-58、107-112 的量縮門檻是 1.05，允許整理期均量比前一段增加 5%。
4. 振幅上限：consolidation_breakout.py:37-40、103-105 的 20 日振幅上限是 30%。

實測結果：
- 2026-09-23 命中 19 檔。第 1 名 2104 距低點 +22.1%，第 2 名 2241 距低點 +43.3%，為當日最遠的一檔。
- 當日距低點最近的是 6189（+9.2%），只排第 12 名。排最後的是 00997A（+13.3%）。
- 2025-10 以後有 228 個交易日至少 3 檔命中。這些日子裡，分數排名與「離低點越遠」排名的 Spearman 平均為 0.62，97% 的交易日為正相關。80% 的交易日第 1 名落在距低點較遠的一半。

門檻寬鬆是事實，但實際命中大多仍符合量縮與窄幅：
- 量縮比大於 1.0 的只占 7.8%，中位數 0.76。
- 20 日振幅中位數 11.3%，大於 20% 的只有 14%。

因此主要問題在評分方向，量縮與振幅門檻的實際影響有限。
    - 理由：我用 app 的純計算函式和本地 enriched parquet，從頭重跑了 consolidation_breakout 的選股與評分。資料為 400 個交易日（2025-02-07～2026-09-23）；matrix pipeline 沿用 DEFAULT_BASIC_FILTER，評分權重與 META 相同。核心數值都完整重現：9/23 共 19 檔命中；第 1 名 2104 距 60 日低點 +22.12%（finding 寫 +22.1%）；第 2 名 2241 為 +43.35%（finding 寫 +43.3%），也是全部命中中距低點最遠的一檔。

程式碼部分也逐項確認：
- distance_from_low_60d 權重 0.3。
- 沒有 override 時方向預設為 high：effective_scoring_directions 回傳 {}，build_matrix_score 只在方向為 'low' 時才反轉。
- low_60d 是含當日在內的 60 日最低收盤價，bottom_zone_max 為 0.45。
- volume_contraction_max 為 1.05，條件是前 20 日均量 ≤ 再往前 20 日均量 ×1.05，也就是允許量增 5%。
- range_max 為 0.30。

「評分偏好遠離低點」在歷史上也確實發生。統計 2025-10-01 以後 228 個至少有 3 檔命中的交易日：
- 分數排名與「離低點遠近排名」的 Spearman 平均為 0.62，97.4% 的交易日為正相關。
- 80% 的交易日，第 1 名落在距低點較遠的那一半。
- 48% 的交易日，距低點最近的那檔排在最後。
- 把方向改成 low 後，相關係數變成 -0.18。

以下幾點與 finding 不符：
(1) 程式碼行號全部錯誤。檔案只有 149 行，finding 引用的 233、188-204、215-222、267-271 都不存在。正確位置是 :73（scoring）、:28-44 與 :96-105（bottom_zone／range）、:55-62 與 :107-112（量縮）。
(2)「距低點最近的（約 +13%）排在最後」不正確。9/23 距低點最近的是 6189（+9.23%），排第 12 名。排最後（第 19 名）的是 00997A（+13.28%），它是第 3 近的一檔；第 2 近的 00402A（+13.26%）排第 10 名。
(3)「量縮條件實際上是量沒有明顯放大」在實際命中裡言過其實。2025-10 以後 4055 筆命中中，只有 7.8% 的量縮比大於 1.0。量縮比中位數 0.76，56.8% 的命中 ≤0.8。9/23 只有 00830 一檔（1.002）大於 1。門檻寬鬆屬實，但實際影響有限。
(4) 30% 振幅上限寬鬆屬實，但實際命中的 20 日振幅中位數只有 11.3%，只有 14.0% 大於 20%。

線上來源：Wikipedia 的 Cup and handle 條目寫明整理期成交量應遞減，支持量縮的前提。finding 引用的 stockalarm 頁面回 HTTP 429；investors.com 與 investopedia 無法抓取；WebSearch 額度已用完。因此「突破量應高出均量 40～50%」這個數字我沒能核實。實際命中的突破量倍數中位數為 2.70 倍，只有 10.3% 低於 1.4 倍。

嚴重度維持 P3：這是策略命名與排序的設計矛盾，不涉及資料錯誤或金融口徑錯誤。另外，9/23 的 19 檔命中中有 10 檔是 ETF（包含槓桿 ETF 00670L）；這不在 finding 的範圍內，只附帶記錄。
    - 證據：
      - backend/app/strategy/builtin/consolidation_breakout.py:73 — "scoring": {"vol_ratio_5d": 0.4, "change_pct": 0.3, "distance_from_low_60d": 0.3}（檔案共 149 行，finding 引用的 :233、:188-204、:215-222、:267-271 都不存在）
      - backend/app/strategy/builtin/consolidation_breakout.py:28-31、96-100 — bottom_zone_max 預設 0.45，above_low = (close-low_60)/low_60 ≤ 0.45
      - backend/app/strategy/builtin/consolidation_breakout.py:37-40、103-105 — range_max 0.30，以前 20 日 high/low 計算（不含當日）
      - backend/app/strategy/builtin/consolidation_breakout.py:55-58、107-112 — volume_contraction_max 1.05：base_volume <= prior_volume*1.05
      - backend/app/backtest/matrix.py:3910-3911 — low_60d = valid_rolling_min(market.close, close_valid, 60)（收盤價，含當日）
      - backend/app/strategy/scoring.py:82-90 — 沒有 override 時 effective_scoring_directions 回傳 {}；backend/app/backtest/matrix.py:3736 只在方向為 'low' 時才執行 1-normalized，所以預設是越遠離低點分數越高
      - 本地重算：cd backend && uv run python AUDIT_SCRATCH/work/verify-cbscore/repro.py → 2026-09-23 命中 19 檔；#1 2104.TWSE score 81.33、above_low 0.2212；#2 2241.TWSE score 66.02、above_low 0.4335（當日最大）；#3 3563 0.3422；#4 2404 0.2438
      - finding 與重算並列：2241 +43.3% vs 重算 +43.35%（相符）；2104 +22.1% vs +22.12%（相符）；19 檔 vs 19 檔（相符）；「距低點最近（約 +13%）排最後」vs 實際最近的是 6189 +9.23%（排第 12），排最後的是 00997A +13.28%（不符）
      - stats.py（2025-10-01 以後，4055 筆命中、237 天）：量縮比 >1.0 占 7.8%（318/4055），中位數 0.761，p90 0.987，≤0.8 占 56.8%；20 日振幅中位數 11.3%，>20% 占 14.0%；above_low 中位數 17.9%，>30% 占 17.0%，>40% 占 4.0%；突破量倍數中位數 2.70，<1.4 倍占 10.3%
      - stats.py（228 個至少 3 檔命中的交易日）：分數排名與距低點遠近排名的 Spearman 平均 0.625、中位數 0.684，97.4% 的交易日為正；80% 的交易日第 1 名落在距低點較遠的一半；48% 的交易日距低點最近者排最後；改成 direction=low 後平均 rho 為 -0.178
      - 9/23 當日量縮比 >1 的只有 00830.TWSE（1.0021）；19 檔中有 10 檔代號以 00 開頭（ETF）
      - docs/radar-strategies-validation.md:80 — `consolidation_breakout` | 1011 | -0.65 | 0.86（PF 0.86 屬實）
      - https://en.wikipedia.org/wiki/Cup_and_handle — "The trade volume should decrease along with the price during the cup and should increase rapidly near the end of the handle when the price begins to rise."（支持整理期量能應遞減）
      - 無法核實：https://pro.stockalarm.io/blog/canslim-growth-stocks-system 回 HTTP 429；investors.com、investopedia.com 無法抓取；WebSearch 額度已用完。因此 O'Neil「突破量高出均量 40–50%」的數字未能獨立核實
      - 工作樹前後比對：git status 在執行前後都只有原本的 3 個修改檔，data/ 沒有新分區

### [P3 → P3] 突破類策略排名前段多為漲停鎖死、隔日難以依收盤買進，評分又加重 change_pct（部分成立）

- **主張**：trend_breakout、boll_breakout、ma_golden 的評分含 change_pct（0.2～0.3）與 vol_ratio，因此漲停股排在前面；策略沒有排除或標示漲停鎖死（委賣為 0）。
- **正確的定義或數值**：至少以 signal_limit_up 標示，或提供『排除漲停鎖死』選項；排序不宜單靠當日漲幅。
- **app 實際行為**：排名最前的都是隔日開盤大多已跳空、無法以選股當時價格買進的標的。
- **對使用者的影響**：依排名操作時，實際進場價與訊號價落差大。
- **修法建議**：在結果列帶出 signal_limit_up 並在 UI 標示；或在評分中降低 change_pct 權重，改為在 limit_up_locked 時扣分。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/trend_breakout.py:44
  - backend/app/strategy/builtin/boll_breakout.py:32
  - backend/app/strategy/builtin/ma_golden_cross.py:41
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 官方 MI_INDEX 9/23：2104、6838、4770、6526、6205、3653、2368、2404、3016、1727 收盤=最高價，最後揭示賣價空白、賣量 0（漲停鎖住）
  - trend_breakout 12 檔中有 4 檔鎖漲停，包含排名第 1 的 2104（score 68.98）；boll 48 檔中有 8 檔；ma_golden 11 檔中有 2 檔
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：trend_breakout（trend_breakout.py:45）、boll_breakout（:32）、ma_golden_cross（:41）的評分都包含 change_pct（權重 0.3／0.3／0.2）和 vol_ratio_5d。評分方式是在當日進場集合內做 min-max 正規化。漲停股的漲幅是集合內最大值，量比通常也最高，所以系統性地集中在前段。

以 9/23 快取的結果為例：
- trend 12 檔中 4 檔鎖漲停，排名 1、2、4、5，第 1 名 2104 的 score 68.98。
- boll 48 檔中 8 檔，排名 1、2、4、5、6、10、11、12。
- ma_golden 11 檔中 2 檔，排名 1、3。

歷史上，收在漲停價的訊號只佔全部訊號的 15～27%，卻佔了前 3 名的 51～71%。

排前面的主因其實是量比。把 change_pct 權重拿掉後，2104 和 6838 在 trend 與 boll 仍排第 2、3 名，6205 在 ma_golden 仍排第 1 名。change_pct 的作用是再推一把。另外，前 3 名並不都是漲停股：每個策略的前 3 名各有 1 檔不是漲停。

策略沒有排除，也沒有標示漲停鎖死：
- basic filter 沒有這個選項。
- 結果列根本不含 signal_limit_up。因為 data/instruments 是空的，screener.py:277-283 會跳過 compute_limit_signals。
- Dashboard 卡片只顯示前 3 名的名稱、收盤價和漲跌幅。

這類訊號在訊號日收盤時沒有委賣，所以無法以收盤價買進。歷史上約 75% 隔日開盤跳空高開，開盤缺口中位數約 +2.1～2.3%，約 21～27% 的缺口超過 5%，只有 6～11% 開在漲停價。

建議的修法：
- 策略歷史路徑要先補上 signal_limit_up，或改讀 enriched 已存的這一欄，結果列才帶得出來。
- 鎖死應以最後揭示賣量為 0 來判定，不能只看收盤價等於漲停價；例如 1235 興泰收在漲停價，但仍有 3 張委賣。
- 再視需要對鎖死的個股扣分，或提供排除選項。
    - 理由：finding 裡的數字全部能重現。另外有三處敘述說過頭，一處程式行號偏一行。

【數字並列（finding → 本次重算）】
- trend_breakout：12 檔中 4 檔鎖漲停 → 12 檔中 4 檔，排名 1、2、4、5。第 1 名 2104 的 score 68.98 → 68.98。
- boll_breakout：48 檔中 8 檔 → 48 檔中 8 檔，排名 1、2、4、5、6、10、11、12。
- ma_golden_cross：11 檔中 2 檔 → 11 檔中 2 檔，排名 1、3。
- 重算方式：用 strategy_cache.json 各列自帶的因子值，按 build_matrix_score 的 min-max 加權公式重算，與快取 score 的最大差是 0.0。
- 鎖漲停的判定：官方 MI_INDEX 9/23 收盤價等於「前收×1.1 再依檔位無條件捨去」的漲停價，而且最後揭示賣價空白、賣量 0。
- 2104 另外用 Yahoo 奇摩核對過：9/23 收 11.65、昨收 10.60、漲 9.91%，委賣五檔全為「-」。
- 另用本地 parquet 的 OHLCV，依 matrix.py 同樣的公式獨立重算 trend_breakout：9/23 的 12 檔與分數完全一致。

【說過頭的地方】
1. 「因此漲停股排在前面」的因果只成立一部分。把 change_pct 權重拿掉重新正規化後，鎖漲停股的名次如下：
   - trend：2104、6838、4770、6526 由 1、2、4、5 名變為 2、3、5、6 名。
   - boll：6838、2104 仍在第 2、3 名；其餘 6 檔由 4～12 名退到 10～29 名。
   - ma_golden：6205 仍是第 1 名。
   換句話說，這些股票排前面，主要是 vol_ratio_5d 太高（7.5～7.6 倍）。change_pct 的作用是放大，不是唯一原因。
2. 「排名最前的都是」鎖漲停股，這不成立。三個策略的前 3 名各有 1 檔不是漲停：trend 第 3 名 2030、boll 第 3 名 3055、ma_golden 第 2 名 3563。
3. 「隔日開盤大多已跳空」方向成立，但幅度不大。以 2024-04～2026-09-22 的歷史訊號、收盤等於漲停價當代理指標，隔日開盤缺口如下：

| 策略 | 隔日跳空高開比例 | 開盤缺口中位數 | ≥5% 比例 | 隔日開在漲停價 |
|---|---|---|---|---|
| trend | 74.7% | +2.08% | 21.3% | 5.6% |
| boll | 75.4% | +2.25% | 25.1% | 8.7% |
| ma_golden | 74.6% | +2.27% | 27.2% | 10.5% |

   同一期間，非漲停訊號的開盤缺口中位數只有 +0.17～0.19%。所以「實際進場價與訊號價有落差」屬實，但多數在 2% 左右，並不是普遍的大幅跳空。
   9/24 當天的開盤還查不到：本地資料只到 9/23，Yahoo 頁面也還停在 9/23 14:30。

【確認成立的部分】
- 歷史上漲停股集中在前段：漲停訊號只佔全部訊號的 27.1%、20.3%、14.5%，卻佔了前 3 名名額的 66.5%、70.6%、51.1%，第 1 名的 71.2%、69.5%、63.8%。
- 策略沒有排除漲停鎖死：compute_signals 裡沒有這類條件，basic_filter 也沒有漲停選項。
- 結果沒有任何標示。

【原記錄沒提到的新面向】
- 快取的結果列裡根本沒有 signal_limit_up 欄位，也沒有 name。原因是 data/instruments 是空目錄，策略歷史在載入時會跳過 compute_limit_signals。雖然 enriched parquet 本身存有 signal_limit_up，歷史載入只讀固定幾欄、重新計算指標，這欄就掉了。所以 fix_suggestion 說的「帶出 signal_limit_up」必須先改歷史載入路徑。
- Dashboard 策略卡預設只顯示前 3 名，每列只有名稱、收盤價、漲跌幅，放大了這個問題的可見度。
- 「收盤等於漲停價」不等於「鎖死」。例如 1235 興泰 9/23 收在漲停價 41.15，但仍有賣價 41.15、3 張的委賣。修正時應以委賣量為 0 判定，不能只看收盤價。

【次要的證據瑕疵】
- trend_breakout 的 scoring 在第 45 行，finding 寫第 44 行。
- local_evidence 列的 10 檔中，2368 不在三個策略的任何名單裡；boll 的第 8 檔 2342 茂矽反而漏列。不過「8 檔」這個計數本身是對的。

【資料版本附註】
enriched 的 9/22、9/23 分區在 22:07 重寫過，晚於快取寫入的 21:40。用目前的 parquet 重算：
- trend 完全一致。
- boll 變成 56 檔，其中 9 檔收在漲停價，1235 排第 1。
- ma_golden 變成 20 檔，前 3 名都收在漲停價。
這不影響結論。

嚴重度維持 P3：這是策略合理性與使用體驗問題，沒有資料錯誤。
    - 證據：
      - backend/app/strategy/builtin/trend_breakout.py:45 "scoring": {"momentum_60d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3}（finding 寫 :44，差一行）
      - backend/app/strategy/builtin/boll_breakout.py:32 scoring: vol_ratio_5d 0.4 / change_pct 0.3 / momentum_20d 0.3
      - backend/app/strategy/builtin/ma_golden_cross.py:41 scoring: momentum_20d 0.5 / vol_ratio_5d 0.3 / change_pct 0.2
      - backend/app/backtest/matrix.py:3676-3742 build_matrix_score：在當日進場集合內做 min-max 正規化後加權，乘 100
      - backend/app/backtest/matrix.py:3641-3673 basic filter 只有價格、市值、成交額、週轉率、ST、板塊，沒有漲停排除選項
      - backend/app/services/screener.py:277-283 compute_limit_signals 只在 instruments 非空時執行；data/instruments 是空目錄，所以 strategy_cache.json 的列欄位沒有 signal_limit_up，也沒有 name
      - frontend/src/pages/Dashboard.tsx:417 STRATEGY_PICK_LIMIT = 3；:510-524 每列只顯示名稱、收盤價、漲跌幅，沒有漲停或鎖死標示
      - 指令：uv run python scratchpad/audit/work/verify-limitup-rank/a_cache_vs_official.py → trend 鎖漲停名次 [1,2,4,5]、boll [1,2,4,5,6,10,11,12]、ma_golden [1,3]；2104 score 68.98；快取 score 與重算的最大差 0.0
      - 同一支腳本的反事實（移除 change_pct 權重）→ trend 鎖漲停股名次 {2104:2, 6838:3, 4770:5, 6526:6}；boll {6838:2, 2104:3, 6526:10, 4770:12, 3016:15, 2404:18, 2342:24, 1727:29}；ma_golden {6205:1, 3653:4}
      - 官方 twse_mi_index_20260923_quotes.csv：2104 close 11.65 = high、ask 空白、賣量 0；6838 close 26.25；4770 close 275；6526 close 840；6205 close 72；3653 close 6410；2342 close 46.35，皆賣量 0；1235 興泰 close 41.15 等於漲停價，但 ask 41.15 × 3 張（收在漲停但未鎖死）
      - https://tw.stock.yahoo.com/quote/2104.TW → 「時間: 2026/09/23 14:30、成交價 11.65、昨收 10.60、漲跌幅 9.91%、委賣全部為「-」」；尚無 9/24 資料
      - 指令：uv run python scratchpad/audit/work/verify-limitup-rank/b_history_nextday.py（本地 enriched parquet 2024-01-02~2026-09-23，共 1,423,597 列）→ trend 收漲停訊號 n=4861：隔日開盤缺口中位數 +2.08%、跳空高開 74.7%、≥5% 21.3%、開在漲停價 5.6%；非漲停 n=13098：缺口中位數 +0.19%
      - 同一支腳本：boll 收漲停訊號 n=9539：缺口中位數 +2.25%、跳空高開 75.4%、開在漲停價 8.7%；ma_golden n=1731：缺口中位數 +2.27%、跳空高開 74.6%、開在漲停價 10.5%
      - 同一支腳本：收漲停訊號佔全部訊號 27.1% / 20.3% / 14.5%，佔前 3 名 66.5% / 70.6% / 51.1%，佔第 1 名 71.2% / 69.5% / 63.8%（依序為 trend / boll / ma_golden）
      - 同一支腳本用本地 OHLCV 獨立重算 trend_breakout 9/23 → 12 檔，名單與分數和快取完全一致
      - data/kline_daily_enriched 的 date=2026-09-23 與 date=2026-09-22 分區 mtime 為 2026-09-23 22:07，晚於 strategy_cache.json 的 updated_at 21:40；用目前 parquet 重算，boll 56 檔（9 檔收漲停）、ma_golden 20 檔（前 3 名皆收漲停）

### [P3 → P3] swing_trend_confirmation 的 min_volume 以股數計，門檻隨股價相差數百倍（部分成立）

- **主張**：min_volume=2,000,000 股（2000 張）。對高價股來說是巨額成交值，對低價股則很低，並不是流動性中性的門檻。
- **正確的定義或數值**：流動性門檻應以成交金額（amount）表示，或以張數搭配價格正規化。
- **app 實際行為**：高價股（如 2454、3653、6669）只有在爆量時才可能入選。
- **對使用者的影響**：這支策略在台股高價權值股上系統性漏報。
- **修法建議**：改用 min_amount（例如 1 億元），或以 20 日均額的倍數表示。
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/strategy/builtin/swing_trend_confirmation.py:66-74
  - backend/app/strategy/builtin/swing_trend_confirmation.py:139-141
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 算術：2000 張 × 2454 收盤 5185 元 ≈ 103.7 億元；2000 張 × 15 元 = 3000 萬元，相差約 346 倍
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：swing_trend_confirmation（以及 pullback_resume）的 min_volume 以股數計，預設 2,000,000 股（2000 張），直接和台股以股為單位的 volume 比對。隱含的成交值門檻等於 2,000,000 × 股價：15 元約 3000 萬，5185 元約 103.7 億，相差約 346 倍，不是流動性中性的門檻。偏差是兩面的：一方面濾掉成交值很大、但股本小的高價股，例如 3653（17 次結構成立被濾 15 次，當日成交值 5.9～12.4 億）、2059 和 5274（全被濾掉）、3008（14 次被濾 12 次）、6669（17 次被濾 8 次，當日成交值 27～49 億）；另一方面保留成交值很小的低價股，共 1,426 次保留下來的訊號當日成交值不到 1 億。6669 在 2026-09-02 除權後，中位成交值只增加約 17%，通過率卻從 31% 升到 88%，證明能不能通過由股本與股價決定，而不是流動性。不過 finding 舉的 2454 並未受影響：近 250 日每天都超過門檻，19 次結構成立全部保留；2330、0050、00878 也一樣，所以不能說「高價權值股」一律被系統性漏報。全市場來看，股價 300～3000 元區間的保留率（46～50%）其實高於低價股（26～31%），只有 ≥3000 元區間明顯偏低（23%）。建議改用成交金額門檻（專案內 trend_breakout 已有 amount_min 的先例），或用均額倍數來表示，並一起修正 pullback_resume。
    - 理由：機制與算術重現成立，但 finding 舉的主例 2454 不成立，「高價權值股系統性漏報」的說法也過度簡化。

一、機制（確認）：swing_trend_confirmation.py:66-74 的 min_volume 預設 2,000,000，標籤寫「(股)」；第 139-141 行直接拿 market.volume 比對。台股矩陣的 volume 是股數：build_market_data_matrix 不做換算（matrix.py:594），volume_units.py:16 對 .TWSE/.TPEX 回傳 "shares"；實建矩陣也顯示 volume_unit=shares。所以隱含的成交值門檻等於 2,000,000 × 股價。finding 的算術正確：2,000,000×5185=10,370,000,000（103.7 億）；2,000,000×15=30,000,000（3000 萬）；5185/15=345.67。

二、主例 2454（不成立）：官方 STOCK_DAY 顯示 2454 在 9/21、9/22、9/23 的成交量分別是 11,237,165、12,131,543、6,430,491 股，都遠高於門檻。本地近 60 日與近 250 日通過率都是 100%，中位量 7.73M／8.68M 股；2024-05-22～2026-09-22 共 19 次結構成立，19 次全保留。2330、0050、00878 也是 0 次被濾掉。

三、確實被濾掉的高價股：受影響的是股價高、成交股數少的個股。結構成立次數對照被濾掉次數如下：3653 是 17 次濾 15 次（被濾當日成交值 5.9～12.4 億）；2059 是 20 次濾 20 次；5274 是 19 次濾 19 次；3008 是 14 次濾 12 次；3529 是 13 次濾 11 次；6669 是 17 次濾 8 次（被濾當日成交值 27～49 億）。6669 近 60 日的通過率是 47%，不是「只有爆量才入選」。6669 還是一組天然對照：官方 TWT49U 記錄它 2026-09-02 除權，前收 7800、參考價 2614.99。除權前（7/1～8/28）中位成交值 99.3 億，通過率 31%；除權後（9/1～9/22）中位成交值 115.8 億，通過率升到 88%。成交值只多約 17%，通過率卻大幅上升，可見能不能通過取決於股本與股價，而不是流動性。

四、全市場重現：用 compute_signals 分別跑 min_volume=2e6 與 min_volume=0，共 28,432 次結構成立，只保留 30.5%。依股價區間的保留率：<20 元 31%、20-50 元 26%、50-100 元 30%、100-300 元 36%、300-1000 元 46%、1000-3000 元 50%、≥3000 元 23%。所以並不是股價越高越吃虧，只有 ≥3000 元這一段最低。偏差是兩面的：被濾掉的結構成立中有 238 次當日成交值 ≥10 億；保留下來的 8,665 次中有 1,426 次成交值 <1 億、520 次 <5000 萬。≥3000 元這一段的 98 次結構成立，當日成交值全部 ≥1 億。官方 2026-09-23 橫斷面（限本地股票與 ETF 範圍，共 2308 檔）：成交值 ≥10 億卻未達 2000 張的有 17 檔（共 162 檔成交值 ≥10 億），包括 3008、3653、3661、5274、2059；≥3000 元區間只有 35.7% 通過股數門檻，但 100% 成交值 ≥1 億。

五、finding 沒提到的面向：pullback_resume.py 有完全相同的 min_volume 預設（第 56-64、118 行）；trend_breakout.py:24 已經用 amount_min=0.2e8（成交金額），專案內有以金額設門檻的先例。docs 裡沒有 min_volume 的既有記錄，不屬於重複回報。

六、對使用者自選股的影響：2454、0050、00878 不受影響。被濾掉的是 3545（14 次濾 9 次）、6196（19 次濾 9 次）、00893（18 次濾 8 次）、00885（17 次濾 3 次）、3034（20 次濾 3 次）、2379（13 次濾 2 次）。這幾檔多半本來成交值就小（0.2～1.8 億），被濾掉也算合理。所以 user_impact 寫的「高價權值股系統性漏報」不適用於這份自選股。

嚴重度：這是參數設計的合理性問題，使用者可以自行調整（範圍 0～50M），不是金融口徑算錯，維持 P3。
    - 證據：
      - 程式碼：backend/app/strategy/builtin/swing_trend_confirmation.py:66-74（min_volume 預設 2000000.0，標籤「最低成交量(股)」）與 :139-141（market.volume >= min_volume）
      - 程式碼：backend/app/volume_units.py:16（台股 volume_unit='shares'）；backend/app/backtest/matrix.py:594（volume 原值進矩陣，不做換算）；實建矩陣輸出「matrix (660, 2346) volume_unit shares」
      - 官方資料 reference\csv\twse_stock_day_202608_202609.csv：2454 在 9/21、9/22、9/23 的成交量為 11,237,165／12,131,543／6,430,491 股，9/23 收盤 5185（和 finding 的價格一致，但成交量遠高於 2,000,000 股）
      - 官方資料 reference\csv\twse_mi_index_20260923_quotes.csv：3653 成交 1,525,519 股、成交值 9,557,795,155、漲 580（接近漲停仍未達門檻）；3008 成交 1,689,986 股、成交值 10,580,864,315；2059 成交 350,946 股、成交值 4,378,788,370；6669 成交 2,904,191 股
      - 本地計算（uv run python scratchpad\audit\work\verify-minvol\analyze.py）的近 60 日通過率：2454=1.0、2330=1.0、6669=0.467、3653=0.167、2059=0.0、5274=0.0、3008=0.633；近 250 日：2454=1.0、6669=0.40、3653=0.272、2059=0.012、3008=0.292
      - 本地計算（scratchpad\audit\work\verify-minvol\signals.py，呼叫 MATRIX_STRATEGY.compute_signals）：min_volume=2e6 時 8665 次、=0 時 28432 次，保留 0.305；各股價區間保留率：<20 元 0.31、20-50 元 0.26、50-100 元 0.30、100-300 元 0.36、300-1000 元 0.46、1000-3000 元 0.50、≥3000 元 0.23
      - 同上：被濾掉的結構成立中，成交值 ≥10 億的有 238 次（共 19767 次被濾）；保留的 8665 次中，成交值 <1 億的有 1426 次、<5000 萬的有 520 次
      - 同上，各股（結構成立次數／保留次數）：2454 19/19、2330 21/21、0050 19/19、6669 17/9、3653 17/2、2059 20/0、5274 19/0、3008 14/2、3529 13/2；自選股 3545 14/5、6196 19/10、00893 18/10、00885 17/14、3034 20/17、2379 13/11
      - 6669 天然對照：官方 reference\csv\twse_twt49u_20260601_20260923.csv 記錄 2026-09-02 除權（前收 7800、參考價 2614.99）；本地計算除權前（7/1～8/28）中位成交值 99.3 億、通過率 0.31，除權後（9/1～9/22）中位成交值 115.8 億、通過率 0.88
      - 官方 9/23 橫斷面（analyze2.py，限本地股票與 ETF 範圍 2308 檔）：成交值 ≥10 億但成交量 <2M 股的有 17 檔（共 162 檔）；≥3000 元區間 2M 股通過率 0.357，成交值 ≥1 億的比例 1.0；門檻對應的中位成交值從 <20 元區間的 0.27 億，到 ≥3000 元區間的 114.92 億
      - 未提及的同型問題：backend/app/strategy/builtin/pullback_resume.py:56-64、118 使用相同的 min_volume=2000000.0；專案內已有以金額設門檻的先例：backend/app/strategy/builtin/trend_breakout.py:24（amount_min 0.2e8）
      - 範圍外旁證（未深查，影響屬推測）：6669 在 9/2 除權後，本地 kline_daily_enriched 的 close 等於 raw_close（8/27 為 6815，9/3 為 2475），data\adj_factor 為空，股價疑似未還原，可能影響依均線判斷的策略；另外本地 2026-09-23 分區沒有任何 TPEX 資料，且 2330 成交量 21,978,873 與官方 22,817,873 不一致
      - 執行前後 git status --porcelain 比對一致，工作樹沒有變更

## 確認正確的項目

- **輸入資料：enriched／taiwan/daily 的 OHLC 與官方一致**：verify_raw.py：官方 STOCK_DAY 共 10 檔 × 38 個交易日（380 列）與本地資料 join，開高低收最大差 0.0；MI_INDEX 9/23 全上市 1323 列與 enriched 9/23 比對，開高低收全部一致（只有 23 列的量不同，見 unverifiable）
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
  - 來源：reference/csv/twse_mi_index_20260923_quotes.csv
  - 來源：data/kline_daily_enriched/date=2026-09-23\part.parquet
- **7 支策略的進場條件（9/23 選股集合）**：indep.py 以逐檔迴圈獨立實作（SMA、手寫 EMA 12/26/9、20 日標準差、收盤價 60 日極值、5 根 pivot、整理區間），不 import 任何 app 程式碼；套用 close≥3、amount≥2e7 後與 strategy_cache.json 比對（compare_cache.py）。結果：bullish 166=166、ma_golden 11=11、macd 10=10、trend 12=12、swing 6=6、consolidation 19=19、boll 48=48，兩邊各自獨有的都是 0 檔
  - 來源：backend/app/strategy/builtin/bullish_alignment.py:57-69
  - 來源：backend/app/strategy/builtin/ma_golden_cross.py:43-56
  - 來源：backend/app/strategy/builtin/macd_golden.py:59-86
  - 來源：backend/app/strategy/builtin/trend_breakout.py:65-76
  - 來源：backend/app/strategy/builtin/boll_breakout.py:53-63
  - 來源：work/A08-strategies-trend/indep.py
  - 來源：work/A08-strategies-trend/compare_cache.py
- **逐日全市場比對（9/3～9/23，15 個交易日，含 9 檔自選）與無未來函數**：app_run.py 用 app 的純函式 build_market_data_matrix＋MatrixStrategyPipeline（自組 panel、不寫檔）跑出每天的進場結果，與獨立實作比對。boll 658/658、ma_golden 186/186、trend 224/224 完全一致；bullish 差 1 筆（1312 在 9/15 float32 下 ma5 與 ma10 同為 14.55）；macd 差 1 筆（3483.TPEX 在 9/22 dif−dea=7e-5，屬 float32 誤差）；consolidation 與 swing 的差異全部來自 finding 2。矩陣裡含有這些日期之後的 K 棒，結果仍與只用過去資料的實作一致，因此這 7 支策略沒有未來函數；swing 用 shift(values, radius) 確認 pivot（swing_trend_confirmation.py:37-39）
  - 來源：work/A08-strategies-trend/app_run.py
  - 來源：work/A08-strategies-trend/debug_diffs.py
  - 來源：backend/app/strategy/builtin/swing_trend_confirmation.py:28-46
- **MA5/MA10/MA20/MA60（收盤價簡單平均）三方比對**：Yahoo chart API（query1.finance.yahoo.com/v8/finance/chart/{2454.TW,3034.TW,00878.TW}）：收盤價與本地逐日完全相同（243/241/243 列，差 0）。排除 Yahoo 在 2026-07-10 的零量補值列（休市，OHLC 等於前一日收盤、volume=0）後，9/23 的 2454 MA5/20/60=4917.000/4531.2500/4067.1667、3034=550.600/544.3000/526.2333，與 app 完全相同。定義來源：Yahoo 奇摩股市文章「N天的收盤價加總後再除以N」、「5日均線（5MA）= 週線…20日均線（20MA）= 月線…60日均線（60MA）= 季線」
  - 來源：https://tw.stock.yahoo.com/news/均線-週線-月線-季線-年線-多頭-空頭-黃金交叉-死亡交叉-124745077.html
  - 來源：https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1y&interval=1d&events=div%2Csplit
  - 來源：work/A08-strategies-trend/yahoo_compare.py
  - 來源：work/A08-strategies-trend/ydates.py
- **bullish_alignment 的定義（MA5>MA10>MA20>MA60＋20 日動能>0）**：與台灣通行定義比對：Yahoo 文章「均線上彎，由上而下依序是最短週期至最長週期」；CMoney 網頁提到短線 5MA/20MA、中線 20MA/60MA。數學上 momentum_20d>0 ⇔ MA20(t)−MA20(t−1)=(c_t−c_{t−20})/20>0，也就是 MA20 上彎，相當於「上彎」條件的一部分（MA5、MA10、MA60 的斜率沒有檢查；CMoney 另外要求『股價站上所有均線』，app 沒有要求 close>MA5），屬於寬鬆變體
  - 來源：backend/app/strategy/builtin/bullish_alignment.py:63-66
  - 來源：https://www.cmoney.tw/learn/course/technicals/topic/488
  - 來源：https://tw.stock.yahoo.com/news/均線-週線-月線-季線-年線-多頭-空頭-黃金交叉-死亡交叉-124745077.html
- **ma_golden_cross 的黃金交叉定義與出場死叉**：程式碼：golden=(ma5>ma20)&(prev ma5<=prev ma20)、dead 為對稱寫法，與 Yahoo 文章「當短期均線向上突破較長期均線…稱為『黃金交叉』」一致；另加 vol_ratio≥1.2、close>MA60 過濾，與 META 描述相符
  - 來源：backend/app/strategy/builtin/ma_golden_cross.py:43-56
  - 來源：https://tw.stock.yahoo.com/news/均線-週線-月線-季線-年線-多頭-空頭-黃金交叉-死亡交叉-124745077.html
- **macd_golden 的參數與交叉定義（run_all 路徑）**：StockCharts：「MACD Line: (12-day EMA - 26-day EMA)」、「Signal Line: 9-day EMA of MACD Line」、「A bullish crossover occurs when the MACD turns up and crosses above the signal line」、「Developed by Gerald Appel」。程式碼 span 12/26/9、DIF 上穿 DEA 相符。run_all 共用 121 根歷史（backend.log『_load_enriched_history(2026-09-23, 121)』），macd_warmup.py 顯示 122 日窗與全歷史在 15 天內只差 1 筆（float32）。柱狀體 ×2 只影響顯示欄 macd_hist，策略沒有使用
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator
  - 來源：backend/app/strategy/builtin/macd_golden.py:64-73
  - 來源：data/backend.log（2026-09-23 21:40:15 行）
  - 來源：work/A08-strategies-trend/macd_warmup.py
- **vol_ratio_5d（量比）口徑**：程式碼：當日量／前 5 日均量（不含當日）（matrix.py 的 vol_ratio_5d 分支；pipeline.py:434-436、486-491）。MBA 智庫的公式為「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」，收盤後累計開市時間等於全日，所以兩者等價。15 天比對中，用到量比的 4 支策略全部一致
  - 來源：backend/app/indicators/pipeline.py:434-436
  - 來源：backend/app/indicators/pipeline.py:486-491
  - 來源：https://wiki.mbalib.com/zh-tw/%E9%87%8F%E6%AF%94
- **評分（scoring）的計算與欄位來源**：score_check.py 依 build_matrix_score 的規則（每天只在當日選股之間做 min-max、權重正規化、×100、只要有一個欄位是 NaN 就整筆給 0）從快取列重算，7 支策略的 max|重算−快取|=0.0000。欄位來源：momentum_20d/60d=close/close.shift(n)−1、change_pct=日報酬、distance_from_low_60d=close/low_60d−1（scoring.py:165-166）
  - 來源：backend/app/backtest/matrix.py:3676-3741
  - 來源：backend/app/strategy/scoring.py:163-166
  - 來源：work/A08-strategies-trend/score_check.py
- **turnover_rate（bullish_alignment 評分 0.3 權重）的數值與單位**：以官方 t187ap03_L 的 issued_common_shares 重算 volume/shares×100：122 檔有值的個股 max 差 3.6e-15。單位是百分比，例如 2454 為 0.40035%。若改用官方最終成交量，2454 為 0.40067%，差異來自 unverifiable 所述的暫定量
  - 來源：reference/csv/twse_t187ap03_L_share_capital.csv
  - 來源：data/user_data/strategy_cache.json
- **台股單位與基本過濾**：volume 單位為股（與官方『成交股數』逐列相符）、amount 單位為元；price_min=3 元、amount_min=2e7 元只有在 matrix 基本過濾中生效（matrix.py:3641 起）；swing 的 min_volume 標示『最低成交量(股)』=2,000,000 股=2000 張，單位一致。ETF 與個股放在同一個 universe（bullish 選股含 00631L 等正 2 槓桿 ETF），與 META asset_types 相符
  - 來源：backend/app/strategy/engine.py:41-54
  - 來源：backend/app/strategy/builtin/swing_trend_confirmation.py:66-74
  - 來源：work/A08-strategies-trend/etf_picks.py
- **swing_trend_confirmation 的波段點定義**：pivot=前後各 radius=2 根的極值，同 Bill Williams 5 根分形。MetaTrader 說明：「a series of at least five successive bars, with the highest HIGH in the middle, and two lower HIGHs on both sides」。app 用 >=，允許平高點，屬寬鬆變體；HH&HL＋MA 多排＋MA20 上彎＋邊緣觸發，與 META 描述一致
  - 來源：https://www.metatrader5.com/en/terminal/help/indicators/bw_indicators/fractals
  - 來源：backend/app/strategy/builtin/swing_trend_confirmation.py:37-40
  - 來源：backend/app/strategy/builtin/swing_trend_confirmation.py:130-145
- **boll_breakout 的意圖與 Bollinger 規則**：bollingerbands.com 規則：「Closes outside the Bollinger Bands are initially continuation signals, not reversal signals」、「In trending markets price can, and does, walk up the upper Bollinger Band」。『收盤突破上軌＋放量＝強勢延續』與此一致；出場條件為收盤跌破下軌。std 口徑見 finding 7
  - 來源：https://www.bollingerbands.com/bollinger-band-rules
  - 來源：backend/app/strategy/builtin/boll_breakout.py:53-63
- **run_all 路徑的歷史長度足以涵蓋各策略暖機**：backend.log 9/23 21:40 run_all 載入 121 根（277,931 列），大於 consolidation 的 100、swing 的 90、其餘的 60；engine.required_history_bars 取所有策略的最大值（engine.py:660-706）
  - 來源：backend/app/strategy/engine.py:660-706
  - 來源：data/backend.log

## 無法驗證的項目

- **9/23 上櫃（TPEX）股票在各策略應有的選股**：本地 9/23 沒有任何 TPEX K 棒（見 finding 1）。官方 tpex_daily_quotes_20260923.csv 只有單日資料，缺少前 120 天的完整上櫃歷史對照，所以沒有重建；只能用 9/22 的占比推估（bullish 69/237、trend 8/25、macd 9/35）
- **vol_ratio_5d 的成交量口徑（是否應含鉅額／盤後定價）**：本地 9/16～9/23 的成交量低於官方最終值（STOCK_DAY 380 列中有 16 列不符，MI_INDEX 9/23 有 23 列不符，本地一律較小，例如 6464 為 2,300 對 302,300）。推測原因：16:30 抓快照時鉅額交易尚未結束（TWSE 鉅額逐筆交易到 17:00，依投資人知識網），而且之後不會重抓。vol_flip.py 顯示 9/23 沒有任何策略的選股因此改變；應以哪一種口徑計算量比沒有權威來源可以裁決，屬資料擷取領域
- **台灣券商（XQ、元大等）的布林標準差口徑**：只查到原作者（population）的說法，台灣券商的文件未取得
- **台灣財經網站顯示的 MACD、布林值（第三方指標比對）**：HiStock、Yahoo 奇摩的技術分析頁由前端動態產生，沒有嘗試抓取；第三方比對改用 Yahoo chart API 的價格自行計算 MA（見 verified_ok）
- **6176 在本地 8/13～8/21 缺 7 根 K 棒的原因**：沒有官方的 6176 月資料可以判斷是停牌還是缺資料；只用它來說明 finding 2 的機制
- **STOP_LOSS／MAX_HOLD_DAYS 等回測參數的合理性，以及歷史 turnover_rate 全部是 null 對回測排序的實際影響**：屬回測引擎領域，沒有追蹤 BacktestEngine 如何處理同分，因此 finding 5 的回測部分標為推測
- **signal_limit_up 與 consecutive_limit_ups 不一致（9/23 的 2104、3653、6526、6838 為 True/0）**：屬漲跌停訊號領域，只記錄現象，沒有追查
