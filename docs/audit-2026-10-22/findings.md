# 問題清單（2026-10-22 全專案審查）

> 基準：HEAD `1724b12`。行號以該版本為準，之後改過的檔案請以函式名稱定位。
> 來源欄是審查範圍代號，對照表見 [verification.md](verification.md)。「驗證」是獨立驗證者的判定：確認＝觸發路徑與程式碼證據都成立；可能成立＝邏輯成立但觸發條件未完全確認。被反駁的宣稱不在本檔，列在 verification.md。

去重後共 337 項：P0 3、P1 6、P2 60、P3 268。嚴重度定義依 CONTRIBUTING.md 第 11 節。

## P0

### B001（P0）未設密碼時 CORS 萬用來源讓任意網頁可操作本機 API

- 位置：`backend/app/main.py:363`
- 區域：核心、資料源與排程；類別：security；驗證：確認；來源：B11-core-infra、X2-security
- 觸發條件：桌面版或本機開發版沒有設定存取密碼（預設狀態），使用者在同一台機器的瀏覽器開啟惡意網頁，且瀏覽器沒有封鎖對本機網路的請求。
- 影響：未設密碼時，本機來源的請求在 main.py:396-399 直接放行；CORSMiddleware 又設 allow_origins=['*']（main.py:363-369），所以任何網站都能讀寫面板 API，包括設定、自選、監控規則，以及 /api/auth/setup（可以搶先設密碼，把擁有者鎖在外面）。自訂資料源的 auth.token_env 可以指定任意環境變數名稱，後端會讀取該值並送到設定的 URL（custom/provider.py:333、346-366），這會讓後端程序的秘密外洩。
- 建議修正：CORS 改成明確白名單（或在未設密碼時拒絕跨來源請求）；token_env 只允許固定的前綴或白名單；更新 main.py:360-362 的過時註解。
- 驗證者意見：以 TestClient 從 127.0.0.1 重現：未設密碼時 auth_middleware（main.py:396-399）放行，CORSMiddleware（main.py:363-369）對外部 Origin 預檢回 ACAO *。跨來源 POST /api/settings/data-sources/test 讓後端讀取任意 token_env 並以 Bearer 送到攻擊者 URL（本機接收端收到假秘密）；跨來源 POST /api/auth/setup 可搶先設密碼，擁有者之後得到 409。main.py:360-362 註解稱走 header 認證，實際為 cookie。緩解：Starlette 會以 400 拒絕帶 PNA 標頭的預檢，Chrome LNA 會跳權限提示；預檢不帶該標頭時照樣成功。未用真實瀏覽器端到端測試。
- 同一問題的其他回報（X2-security，評 P2，位置 `backend/app/main.py:365`）：CORS allow_origins=['*'] 搭配錯誤注解(宣稱走 header 認證,實為 cookie 認證)；驗證者意見：main.py:362 的註解說認證走 header，但 auth_middleware 只讀 cookie（main.py:410），註解錯誤屬實。宣稱「目前無實際洩漏」低估了風險：未設密碼時本機/內網 IP 直接放行（main.py:395-397）。我用 TestClient 指定 暫存目錄 資料目錄實測，帶外站 Origin 的 OPTIONS 與 GET /api/settings/data-sources 都回 200，且帶 ACAO:*。也就是本機瀏覽器開的任何網頁都能跨站讀寫 API，瀏覽器的 Local Network Access 提示只能部分緩解，因此上調為 P2。

### B002（P0）screener 自訂條件走 DuckDB 字串拼接，可讀寫主機任意檔案（含 auth.json）

- 位置：`backend/app/services/screener.py:558`
- 區域：後端服務；類別：security；驗證：確認；來源：B9b-services-app、B10b-api-app、X1b-dead-endpoints
- 觸發條件：呼叫 POST /api/screener/run（前端 api.screenerRunCustom），conditions 傳入惡意 SQL 片段，例如 `1=0) UNION ALL BY NAME SELECT content AS symbol FROM read_text('.../data/user_data/auth.json') --`，或 `1=1); COPY (SELECT 'x') TO '任意路徑' (HEADER false); SELECT * FROM enriched WHERE (1=1`。（勘誤：前端 api.screenerRunCustom 只有定義、沒有任何呼叫者，需直接呼叫 POST /api/screener/run 才會觸發。）
- 影響：ScreenerService.run 把使用者 conditions/order_by 直接字串拼進 `SELECT * FROM enriched WHERE {where} ORDER BY {order_by}`。雖用獨立 :memory: 連線隔離業務表，但 DuckDB 預設開啟 external access：注入可用 read_text/read_csv/read_parquet 讀主機任意檔（我實測讀出偽造的 auth.json 內容），並用 COPY ... TO 寫任意檔（我實測成功落檔）。可竊取 auth.json 內的密碼雜湊+鹽+所有 session token，或寫檔達成持久化/RCE。違反 CONTRIBUTING §8『不用不安全的字符串拼接執行 SQL』。原碼註解已知可注入，但低估為『至多碰 read_csv/read_parquet 檔』，實際可任意讀寫。
- 建議修正：conditions 不應是原始 SQL。改用參數化/白名單化的欄位+運算子+值結構；或在建立連線後執行 `SET enable_external_access=false`（並關閉 read_text/COPY），同時對 order_by 僅允許已知欄位名與 asc/desc。
- 驗證者意見：宣稱成立。screener.py:558-561 把使用者 conditions/order_by 原樣字串拼成 `SELECT * FROM enriched WHERE {where} ORDER BY {order_by}`；api/screener.py:250-260 的 run_custom 將 CustomRequest.conditions/order_by 透傳，CustomRequest（35-36 行）無任何驗證器，db_safe 的 is_valid_ext_ident/quote_ident 只用於 ext_columns，不碰 conditions。既有測試（test_stage2/test_stage4/test_taiwan_forward_adjustment）打 /api/screener/run 只傳 conditions=[]，無任何測試斷言防注入，故無反證。我以專案 venv 的 DuckDB 1.5.3 複現同一拼接邏輯：(A) 注入 `read_text(...)` UNION 讀出偽造密鑰檔內容；(B) 經 order_by 多語句 `COPY ... TO` 成功寫出 written.txt；且 enable_external_access 預設為 True，repo 無處關閉。觸發面：服務預設綁 0.0.0.0，未設密碼時 LAN 請求經 middleware 直接放行，單一 POST 即可任意讀寫主機檔（竊取 auth.json 內雜湊+鹽+session token，或寫檔持久化/RCE）；設密碼時需登入。違反 CONTRIBUTING §8。依 §11「安全漏洞」屬 P0，原報 P1 低估。審查文件未記錄此問題。
- 同一問題的其他回報（B10b-api-app，評 P2，位置 `backend/app/services/screener.py:558`）：/api/screener/run 把使用者 SQL 片段字串拼接進 DuckDB，可多語句執行與讀寫本機檔案；驗證者意見：services/screener.py:558-564 把 conditions 和 order_by 直接拼成 SQL 字串，在 :memory: DuckDB 執行。我在 暫存目錄 用 duckdb 1.5.3 實測：注入 `1=1); COPY ... TO '<path>'; SELECT ...` 確實寫出檔案，read_text 子查詢也能拿來當布林預言機讀檔。路由已在 main.py:422 註冊。小修正：api.ts:2759 有 screenerRunCustom 包裝，只是沒有任何呼叫者。main.py:384-415 的認證限制只有已登入者，或未設密碼時的內網來源才能打到這個端點，所以維持 P2。
- 同一問題的其他回報（X1b-dead-endpoints，評 P0，位置 `backend/app/services/screener.py:559`）：POST /api/screener/run（前端已不使用）把使用者傳入的 SQL 片段直接拼進 DuckDB 查詢；驗證者意見：screener.py:558-564 把 conditions 與 order_by 原樣拼進 SQL 後 con.execute；api/screener.py:34-41、249-266 無驗證；main.py:422 註冊 router。在 暫存目錄 以同 venv duckdb 1.5.3 照相同組字實測：經 conditions 與 order_by 的子查詢 read_csv 可讀檔，跳出括號的多語句 COPY TO 可寫檔。前端 api.ts:2759 screenerRunCustom 只有定義、無呼叫端。違反 CONTRIBUTING 第 8 節禁止拼接 SQL。

### B003（P0）策略程式碼 AST 安全白名單可被繞過，存檔即可執行任意程式碼

- 位置：`backend/app/strategy/ai_generator.py:377`
- 區域：策略、指標與監控；類別：security；驗證：確認；來源：B8a-strategy-monitor、X2-security
- 觸發條件：呼叫 POST /api/strategies/code/save（或 /api/strategies/ai/save）時，送出只用到白名單模組屬性鏈的程式碼。例一：`import numpy.ctypeslib as c; c.ctypes.CDLL("msvcrt").system(b"...")`。例二：`import polars as pl; pl.DataFrame({...}).write_csv("<任意路徑>")`。存檔流程會寫檔並 engine.reload()，_load_file 隨即 exec_module。另外，有人直接放進 data/strategies/* 的同類檔案，也會在重新載入時執行。
- 影響：可在後端行程內執行任意程式碼或寫入任意檔案（寫入開機啟動資料夾、覆寫資料、讀取機密），屬安全漏洞。auth_middleware 在尚未設定密碼時放行所有內網 IP（main.py:394-398），CORS 又是 allow_origins=['*']、allow_headers=['*']（main.py:363-369），所以同網段任何主機都能觸發。瀏覽器跨來源觸發則取決於瀏覽器是否強制 Private Network Access（推測）。
- 建議修正：不要把 AST 白名單當成安全邊界。使用者與 AI 產生的策略改在受限子行程執行（不給檔案系統與網路權限），或改為只接受宣告式設定。過渡期至少把模組白名單改成精確的屬性白名單（禁止 ctypeslib 與 read_*/write_*/sink_* 等 I/O），未設密碼時拒絕非本機的寫入類 API，並把 CORS 收斂到前端來源。
- 驗證者意見：以執行證據確認。_module_allowed（ai_generator.py:377-381）只比對完整名或第一段，故 numpy/polars 的所有子模組放行。我用 backend/.venv（Python 3.11，PYTHONDONTWRITEBYTECODE=1、-I、DATA_DIR 指 暫存目錄、不連網、不寫 repo）只跑「靜態」_validate_safety（它 ast.parse 不執行代碼），實測五個樣本全 PASSED：import numpy.ctypeslib、from numpy.ctypeslib import ctypes、polars write_csv/read_csv 參照、c.ctypes.CDLL 參照；負向對照 import os 正確 BLOCKED。環境 numpy 2.4.6 具 numpy.ctypeslib.ctypes.CDLL、polars 1.40.1 具 write_csv/read_csv（屬性鏈與 I/O API 皆不攔）。觸發鏈：strategy.py:707 校驗→816 寫檔→819 reload→engine.py:443 再校驗→475 exec_module，模組頂層碼即執行（exec 語意）。既有測試僅斷言擋 import os（test_strategy_code_save.py:59），未覆蓋子模組/屬性鏈/I-O，故宣稱對、測試不足而非測試錯。可觸及性採驗證者一修正：Dockerfile:106 綁 0.0.0.0、未設密碼時內網 IP 放行（main.py:396-399）、CORS 全開（363-369），Docker 部署同網段可觸發；desktop.py:225 僅 127.0.0.1。docstring 自承非沙箱（363-364），docs 查無此條。維持 P0。
- 同一問題的其他回報（X2-security，評 P1，位置 `backend/app/strategy/ai_generator.py:404`）：AI/自訂策略 AST 安全檢查可輕易繞過,存檔即在後端行程 exec 任意程式碼 (RCE)；驗證者意見：重現成功：呼叫攔截只比對 ast.Name 直接呼叫（ai_generator.py:403-405）。e=exec、[exec][0]、o=open、polars write_csv、import numpy.ctypeslib 都通過 _validate_safety、engine 複檢（engine.py:443-446）與 /code/save 的 _prepare_strategy_code，之後由 exec_module（engine.py:475）執行。另起獨立行程執行這些 payload，皆能寫檔。攻擊前提是面板權限（已登入，或未設密碼時的內網放行窗口），把影響從面板擴大到整台主機；寫 Python 策略本是擁有者功能，故評 P1。

## P1

### B004（P1）Docker 容器啟動即失敗：app/__init__.py 讀取 /VERSION，但 Dockerfile 只把 VERSION 複製到 /app/VERSION

- 位置：`backend/app/__init__.py:7`
- 區域：核心、資料源與排程；類別：build；驗證：確認；來源：I1-infra-config-deps
- 觸發條件：依 README 方式 B 執行 `docker compose up --build`，或執行 docker.yml 推到 ghcr 的映像。uvicorn 匯入 app.main 時會先執行 app/__init__.py
- 影響：容器內 `Path('/app/app/__init__.py').resolve().parents[2]` 是 `/`，於是讀 `/VERSION`，拋出 FileNotFoundError，uvicorn 無法啟動；`restart: unless-stopped` 讓容器不斷重啟。建置階段沒有任何 RUN 匯入 app，所以 `docker build` 與 docker.yml 都會成功，並把壞掉的映像推到 :latest。
- 建議修正：Dockerfile 改為（或另外）`COPY VERSION /VERSION`；或讓 app/__init__.py 依序嘗試 _MEIPASS、parents[1]、parents[2]，也可以接受 VERSION_FILE 環境變數。
- 驗證者意見：容器內 __file__ 為 /app/app/__init__.py，parents[2] 為 /，讀 /VERSION；Dockerfile:88 只複製到 /app/VERSION。暫存目錄 模擬容器目錄結構以 uvicorn.importer 匯入，重現 FileNotFoundError；對照組補上 /VERSION 後匯入成功。未執行 docker。

### B005（P1）取客戶端 IP 取 X-Forwarded-For 最左值，反向代理後會被偽造，繞過 setup 閘門與登入限流

- 位置：`backend/app/api/auth.py:77`
- 區域：API 層；類別：security；驗證：確認；來源：B10b-api-app
- 觸發條件：面板依文件部署在反向代理（Nginx）之後（docs/deploy-password.md:85 的建議部署）。直連 peer 是代理（127.0.0.1），_is_local_network(direct) 為真，於是採信 XFF 並取 `xff.split(",")[0]`（最左）。標準 nginx `proxy_add_x_forwarded_for` 會把真實來源 append 在最右，最左是用戶端可自填的值。
- 影響：攻擊者送 `X-Forwarded-For: 127.0.0.1` 即可讓 _client_ip 回傳 127.0.0.1：(1) 未設密碼時通過 main.py:398 的本機/內網閘門，從公網搶先呼叫 /api/auth/setup 設定密碼（正是此設計要防的攻擊）；(2) 已設密碼時，攻擊者每次請求換一個偽造 XFF 即得到全新的失敗計數 key，_check_login_rate_limit 永遠不觸發，完全繞過「錯 5 次鎖 5 分鐘」的暴力破解保護。正確作法是取可信代理 append 的最右值或限定信任的 proxy 跳數。
- 建議修正：不要取最左 XFF。若部署在單層可信反代，取 XFF 最右（代理 append 的值）或改用 ASGI ProxyHeadersMiddleware/明確配置可信 proxy 數；在沒有可信反代設定時，不採信 XFF 而只用直連 peer。
- 驗證者意見：以真實 app 加 uvicorn 0.47 預設包裝執行。同機 Nginx 路徑不成立（uvicorn 已用 XFF 最右值改寫 client）。官方 Docker 部署前加 Nginx 時 peer 為 172.x 閘道，uvicorn 不改寫，auth.py:76-77 採信最左值：偽造 127.0.0.1 通過 main.py:398 閘門，setup 回 200 搶到密碼；輪換最左值 30 次錯誤登入都沒有 429。需 Docker 或異機反代才觸發，維持 P1。閘道與 XFF 串接為模擬值，未實機架設。

### B006（P1）組合模式次日開盤買進時，會用同一天稍晚才釋放的倉位與資金（盤中停損、收盤賣出、分鐘賣出）

- 位置：`backend/app/backtest/engine.py:2033`
- 區域：回測與挖掘；類別：look-ahead；驗證：確認；來源：B6-backtest-engine
- 觸發條件：entry_fill=open_t+1（前端與 API 預設 matching=open_t+1），持倉滿額或現金受限，且同一天有持倉在開盤之後才出場：停損、移動停損、停利以觸價線成交（low/high 觸及，非開盤跳空），或 exit_fill=close_t／signal_next_minute 的賣出
- 影響：迴圈在同一個 bar 先處理風控與訊號出場（1968-2031），再以 matrix.open 買進新標的（2033-2109）。開盤時根本還不知道這些倉位稍後會出場，回測卻已經把空出的倉位與資金拿去開盤買進。重現結果：max_positions=1，A 在 2024-01-04 盤中以 9.5 停損，B 卻在同日以開盤價 10.0 買進，buy_no_slot=0。這會高估資金使用率與報酬，屬於資金面的未來函數
- 建議修正：open_t+1 進場時，先撮合開盤時點的出場（開盤跳空停損、open_t+1 出場），再做開盤買進；盤中觸價、收盤、分鐘出場要等買進之後才處理，或把它們釋放的倉位與現金延到下一個 bar 才可用。補一個倉位滿額、同日盤中停損的測試
- 驗證者意見：我獨立重寫腳本並實際執行，宣稱成立。_simulate_portfolio_matrix 每個 bar 的處理順序是：1968-2012 風控出場、2014-2031 訊號／max_hold 出場、2033 之後才以 matrix.open（1693）買進，而且用的 len(positions) 與 cash 已經扣掉同日稍晚才出場的倉位。

我做了兩組對照。S0：停損沒觸發時 buy_no_slot=1，證明開盤當下倉位確實是滿的。S1：low 觸及停損線之後，B 在同日開盤就買進，buy_no_slot=0。盤中停利（S6）、close_t 訊號出場（S3）、close_t max_hold（S4）都能重現同樣的現象。開盤跳空停損（S2）和 open_t+1 出場（S5）在開盤就成交，騰出的倉位當天拿來買進是合理的。

既有測試 test_failed_open_exit_keeps_slot_and_blocks_replacement_buy（test_engine_portfolio.py:93）只涵蓋開盤出場失敗，沒有斷言相反的行為；該檔 14 個測試全數通過，沒有測試涵蓋盤中或收盤出場後同日買進的情境。API 預設 matching=open_t+1（api/backtest.py:179），只要設了停損或停利就會觸發。依 CONTRIBUTING 第 11 節，未來函數屬 P1。

### B007（P1）「觸發後下一分鐘成交」只在日線收盤已確認跌破的日子回放，形成未來函數

- 位置：`backend/app/backtest/matrix.py:2529`（審查者原標 `backend/app/backtest/engine.py:1922`）
- 區域：回測與挖掘；類別：look-ahead；驗證：確認；來源：B6-backtest-engine
- 既有文件：docs/audit-2026-09-24/sections/A11-backtest-costs.md:981 只確認觸價線只用當日已知資料，沒有記錄「回放日由收盤決定」的選日偏差
- 觸發條件：API 傳入 minute_fill=true、exit_fill=signal_next_minute，賣出訊號為 signal_ma5/ma10/ma20_breakdown 或 signal_ma_dead_5_20（matching 預設 open_t+1）
- 影響：盤中觸價線本身只用當日已知資料，但要不要在盤中賣，是由「當日收盤是否收在均線下」的日線訊號決定的（signals.exit != 0）。結果有兩個偏差：(1) 盤中跌破、收盤又站回的假跌破日，即時規則會賣出，回測完全不賣；(2) 真跌破日改用盤中第一次跌破後的下一分鐘開盤價，通常比收盤價好。測試樣本在收盤 9.0 的日子用 9.7 賣出。勝率與報酬都被系統性高估
- 建議修正：盤中回放要在每個持倉日都檢查分鐘觸價（觸價線用前一日以前的資料），不能用日線 exit 當作回放的前提；做不到時應拒絕 signal_next_minute 或在結果標示為非可交易口徑，並補一個「盤中跌破、收盤站回」的負例測試
- 驗證者意見：重現成立。signal_next_minute 時 exit_delay_bars 為 0（engine.py:1093、strategy.py:1843），所以 matrix.exit[t] 就是當日收盤才確定的跌破訊號（pipeline.py:787-789）。只有這些格子會覆寫觸價線（matrix.py:2529-2530）、預載分鐘 K（engine.py:1133-1135）、在 reason=="signal" 時呼叫 _minute_trigger_price（engine.py:1294-1300、1922-1928）。我做了兩個情境，盤中觸價線都是 10.0、早盤分鐘 K 完全一樣，只有收盤不同。假跌破日（收盤 10.1）根本沒讀分鐘 K，也沒有賣出，部位抱到期末；即時規則則會在 09:33 以 9.7 賣出。真跌破日（收盤 9.0）以 9.7 成交。也就是「要不要在盤中賣」取決於 13:30 的收盤，違反 CONTRIBUTING.md:167，依 306 屬 P1。既有測試 test_engine_portfolio.py:449-485 只有真跌破的正例，寫死了這個行為，缺少負例，所以是測試覆蓋不足，宣稱本身沒錯。觸價線代數正確（A11:981），但只能保證成交價沒有用到未來資料，選日偏差仍在。前端 api.ts:2018/2032 只有型別定義，要直接呼叫 API 才會觸發。

### B008（P1）vwap_bias 用前復權收盤價除以原始價尺度的 VWAP，歷史因子值被除權息幅度主導

- 位置：`backend/app/backtest/matrix.py:4415`
- 區域：回測與挖掘；類別：data-contract；驗證：確認；來源：B5-backtest-matrix
- 觸發條件：任何讀取歷史日 vwap_bias 的路徑都會觸發：排程挖掘預設的 FACTOR_COLUMNS[:48] 就含 vwap_bias、mining IC、factor_rank_research 與用 vwap_bias 評分的回測。只要標的在該日之後有過現金股利（前復權因子 <1），該日的值就失真。
- 影響：在 data/kline_daily_enriched 全量 1,464,920 列實測：失真的絕對值中位數 3.5%、p90 13.1%、p99 47.9%，真實 vwap_bias 絕對值的中位數只有 0.4%，誤差約是訊號的 9 倍。2024-01-02 有 82% 標的偏差超過 1%，例如 0056.TWSE 算出 -24.39%（正確值 -0.01%），2330.TWSE 算出 -3.80%（正確值 +0.32%）。截面排名實際上變成在排累計股利率，挖掘出的 IC、候選與回測評分都錯。最新交易日的因子為 1，不受影響，所以從當日選股看不出來。
- 建議修正：用同一個價格尺度計算：以 close/raw_close 把 amount/volume 轉成前復權尺度後再比較，或改用 raw_close 對原始成交股數算出的 VWAP。台股成交量會隨股數型事件調整，所以要確認分母用的是未調整股數。matrix 與 scoring 兩條路徑同步修改，並補一個跨除權息日的單元測試。
- 驗證者意見：我用執行結果重現了這個問題。既有測試（test_taiwan_backtest.py:64、test_factor_library_v2.py:98/112、test_matrix_strategy.py:400）的資料都是 close 等於原始價，沒有一個跨除權息日，也沒有測試斷言相反的行為。合成案例直接走正式的 _apply_adj_factor：每天都以單一價格成交，正確值應全為 0，但除息前兩條路徑都算出 -5%。真實資料改走正式的 parquet loader（快取關閉、唯讀），數字和宣稱一致。2024-01-02 的截面上，這個因子和累計復權幅度的名次相關是 0.957，和正確值只有 -0.01，所以排名排的其實是股利率。最新一日誤差為 0，從當日選股看不出來。這是錯誤的金融口徑，依第 11 節判 P1。修法要注意：股數型事件的 volume 已經乘過 volume_factor，再乘 close/raw_close 會重複調整。

### B009（P1）mining 交叉評估把後期折的勝出者拿到早期折測試窗評分，樣本內資料被當成 OOS 計入候選指標與發佈門檻

- 位置：`backend/app/backtest/mining.py:1242`
- 區域：回測與挖掘；類別：look-ahead/資料洩漏；驗證：確認；來源：B7-mining-optimizer-workers
- 觸發條件：至少兩個外層折選出不同勝出者（len(winner_union) > 1）。以 balanced 為例（outer_train 504、purge 30、test 126、step 63）：第 3 折勝出者的選取窗是 labels[189:693]，卻被拿到第 0 折的測試窗 labels[534:660] 評分，這段完全落在它自己的選取窗內。exploratory（126/30/63/63，本機 678 個交易日可切 8 折）也一樣：第 2 折的訓練窗 [126:252] 涵蓋第 0 折的測試窗 [156:219]。
- 影響：候選的 oos_sharpe、oos_return、oos_positive_fold_ratio、valid_folds、oos_n_trades 混入了樣本內的評分窗，排名（_rank_artifact_candidates）與發佈門檻（GATE_MIN_VALID_FOLDS=2 等）都可能只靠樣本內的折通過，過擬合的因子組合因此被標成通過 OOS 驗證並發佈到策略庫。屬於 CONTRIBUTING 第 11 節定義的未來函數與錯誤金融口徑。目前前端沒有挖掘頁，只有直接呼叫 /api/backtest/mining 才會觸發；若依 A09 稽核以可達性降級的慣例，可視為 P2。
- 建議修正：只把來自第 k 折的勝出者交叉評估到 j > k 的折（第 j 折測試窗的起點晚於第 k 折 train_end+purge）；或把 j <= k 的評分標成 in-sample，不計入 oos_* 與門檻。
- 驗證者意見：重現成功。mining.py:1242-1284 把每個 winner 都拿到所有折的測試窗評分，沒有檢查折序。在 balanced 與 exploratory 兩種設定下，j<k 時第 j 折的測試窗都會和第 k 折的訓練窗重疊，j>k 則一組都沒有。用真實的 MiningService.run 加上 _build_artifacts 實測：只在選取窗內有效的 noise 因子，在第 0 折得到樣本內的 sharpe 2.0，以 cross 列身分算進 valid_folds 與 oos_*，結果排名勝過 good 並通過 evaluate_candidate_gate。排除這一列後 pos_ratio 只剩 0.5，門檻不通過。既有測試 test_mining.py:798 的折與折互不重疊（step=10），不會出現 j<k 的重疊，所以和宣稱不衝突，只是沒涵蓋到。CONTRIBUTING 第 11 節把未來函數列為 P1，所以維持 P1。

## P2

### B010（P2）Linux 桌面產物沒有 pywebview 的 GUI 後端（GTK/Qt），無法開窗

- 位置：`.github/workflows/release.yml:115`
- 區域：建置與部署；類別：ci；驗證：確認；來源：I1-infra-config-deps
- 觸發條件：以 platforms=linux 執行 release.yml，使用者解壓並執行 ItoyStockPanel-linux-x64.tar.gz
- 影響：pywebview 在 Linux 需要 PyGObject(GTK) 或 qtpy+PyQt/PySide。desktop extra 只安裝 pywebview，ubuntu runner 的 uv 環境沒有 gi 或 Qt 綁定，PyInstaller 也就打包不到。webview.start() 找不到 GUI 後端時拋出例外，desktop.main 走 _show_crash 並回傳 1。README 與 Release 頁列出的 Linux 安裝檔無法使用。
- 建議修正：Linux 建置改裝 `pywebview[qt]`（或 gtk 並在 runner 安裝 libgirepository/webkit2gtk），並在 CI 加一步無頭啟動冒煙測試；或在 Linux 改成以系統瀏覽器開啟 http://127.0.0.1:port。
- 驗證者意見：release.yml:115 只裝 desktop extra，而 pyproject:53-55 只有 pywebview。uv 快取中 pywebview 6.2.1 的 metadata 顯示 pygobject、pyqt6、pyside6 都只在 gtk/qt 等 extra 才會安裝；uv.lock 只有標記 openbsd6 的 qtpy，沒有 Qt 或 GTK 綁定。uv 管理的 Python 也看不到系統的 gi，凍結產物在 Linux 沒有 GUI 後端可用。workflow 預設只建 windows，要手動選 linux 才會觸發。未實跑 Linux 產物。

### B011（P2）/api/kline/minute-range 不回傳 unattributed_volume，多日分時的均價覆蓋率恆為 100%

- 位置：`backend/app/api/kline.py:1115`
- 區域：API 層；類別：data-contract；驗證：確認；來源：B10a-api-data
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md G05-gap#2（RC18，標為已修正，引用 EChartsMultiDayIntraday.tsx:75-76）；A13-realtime#6 新增 unattributed_volume。兩者都沒有涵蓋 minute-range 後端欄位缺漏
- 觸發條件：開啟多日分時圖（StockMultiDayIntradayChart，例如 5 或 10 日），且最新一日以外的某個交易日在錄製時有缺口（後端盤中重啟、輪詢中斷超過 60 秒），minute_recorder 已把缺口量寫進該日分區的 unattributed_volume。
- 影響：歷史交易日的 rows 一律缺 unattributed_volume。前端 computeVolumeCoverage 因此把缺口量當 0，覆蓋率算成 100%，均價線照常畫出並標「均價覆蓋 100%」。依 MIN_AVERAGE_COVERAGE=0.8 的規則，這些交易日本來應該隱藏均價線。G05-gap#2 的修正在多日圖上對最新日以外的交易日都沒有作用；最新日走 /api/kline/minute，不受影響。
- 建議修正：在 repository.get_minute_range 的 select_cols 加入 unattributed_volume（_scan_minute 已保證這一欄存在），或在 minute-range 改用不裁欄的讀法。補一個測試：minute-range 回傳的歷史交易日要帶出 unattributed_volume。
- 驗證者意見：親自讀碼確認：repository.py:1835 的 select_cols 固定為 8 欄，沒有 unattributed_volume；_scan_minute（:1777）雖補上這一欄，但被 select 裁掉。kline.py:1124-1130 只在欄位存在時才帶出，所以歷史交易日一律缺這一欄。前端 computeVolumeCoverage（intraday-chart.ts:38-48）因此把缺口量當 0，覆蓋率算成 100%；EChartsMultiDayIntraday.tsx:77-78 依此決定是否顯示均價線，:457 也會顯示覆蓋率。只有最新一日被 /minute 覆蓋，不受影響。屬明確功能錯誤，維持 P2。

### B012（P2）監控引擎的盤中策略結果在收盤後永久覆蓋盤後全市場策略快取

- 位置：`backend/app/api/screener.py:329`
- 區域：API 層；類別：cache-consistency；驗證：確認；來源：X3-concurrency-cache-time
- 既有文件：docs/audit-2026-09-24/sections/A12-screener-stats.md:289、:301 只描述了這條疊加路徑，當時使用者沒有監控規則，所以沒有列為缺陷
- 觸發條件：有任一條啟用的 type=strategy 監控規則，交易日即時行情開著。13:30 之後收盤那一輪 evaluate 只對收盤結果已到的標的計算；16:30 或 21:40 排程寫入官方全市場 strategy_cache.json 後，使用者打開首頁或策略頁。
- 影響：/api/screener/cached 用 monitor_engine 記憶體裡的結果整個覆蓋同一策略的盤後結果。被監控的策略在首頁「策略當日推薦」與策略頁上，只剩輪詢池（≤300 檔自選加規則標的，收盤輪更只剩 _close_final_symbols）的盤中估算結果，看不到官方收盤後的全市場選股。這個狀態會一直維持到下一個交易日 09:00 第一輪連續交易評估；週末是從週五 14:30 一路維持到週一 09:00。
- 建議修正：只有在連續交易時段，且 realtime 結果的 as_of 晚於快取 as_of 時才疊加；或者在 refresh_strategy_cache 寫入後清除 monitor_engine 的 _latest_strategy_results，並在收盤後停止疊加。
- 驗證者意見：screener.py:329 的 results.update 沒有比對 as_of。monitor.py:1063 寫入的是輪詢池 df 的結果（repository.py:2548、2604 盤中只放輪詢池的列），收盤輪再經 _closing_scope 縮小。_take_closing_evaluation 每天只會成立一次，之後 evaluate 不再執行，盤後排程也不會清掉這份結果。被監控的策略會用子集覆蓋全市場結果，直到隔天 09:00。

### B013（P2）新建自訂訊號時，若標識與既有訊號相同，會直接覆蓋舊訊號而不提示

- 位置：`backend/app/api/signals.py:115`
- 區域：API 層；類別：資料遺失／前後端不一致；驗證：確認；來源：GAP3-user-customization
- 觸發條件：在設定頁點「新建自訂訊號」，輸入與既有訊號相同的標識後儲存。例如兩次都照輸入框的範例填 low_touches_ma5。
- 影響：舊訊號的名稱、類型與條件被整份取代，畫面沒有任何提示。引用 csg_<id> 的策略與監控規則會改用新條件評估，回測與告警結果靜默改變。
- 建議修正：後端把新建與更新分開：POST 遇到已存在的 id 回 409，更新改走 PUT /{id}。前端新建前先比對 customSignals 清單並提示。
- 驗證者意見：signals.py:115-124 的 POST 不分新建與更新，直接呼叫 save_one；custom_signals.py:92-95 用 write_text 覆寫。CustomSignalDialog.tsx:80-90 新建時只驗格式，CustomSignals.tsx:49-54 也不比對清單。全程沒有存在檢查。CONTRIBUTING §12 把覆蓋用戶資料列為不可合併。

### B014（P2）刪除或停用自訂訊號時不檢查引用，引用它的監控規則從此不再觸發，也沒有任何提示

- 位置：`backend/app/api/signals.py:165`
- 區域：API 層；類別：監控／訊號鏈；驗證：確認；來源：GAP3-user-customization
- 觸發條件：已有監控規則的條件是 {field:'csg_x', op:'truth'}。使用者在設定頁連點兩次刪除訊號 x（frontend/src/pages/settings/CustomSignals.tsx:78-87），或按 Zap 停用（:74-76）。
- 影響：停用後 build_expressions 直接跳過該訊號（custom_signals.py:204-205），刪除後也不再有 csg_x 欄。監控引擎的 _condition_mask_expr 遇到缺欄回 None，命中結果為空（backend/app/strategy/monitor.py:247-248、268-270、1243-1244），規則永久不告警，也不寫日誌。
- 建議修正：刪除或停用前掃描監控規則與策略中的 csg_ 引用，回 409 或要求使用者確認。list_rules 對缺少的 csg_ 欄加 runtime_warning。monitor_rules.validate 檢查 csg_ 訊號存在且已啟用。
- 驗證者意見：signals.py:165-173 只刪檔與清快取；custom_signals.py:204-205 停用的訊號不編譯。monitor.py:247-248、1243-1245 缺欄時回空結果且不記日誌。api/monitor_rules.py:259-268 只對分組加警示，monitor_rules.py:184-187 只驗前綴。觸發路徑完整成立。

### B015（P2）進場當根同時出現賣出訊號時，組合模式直接丟掉訊號，全量模式則延後一日賣出，兩種模式結果不一致

- 位置：`backend/app/backtest/engine.py:2134`
- 區域：回測與挖掘；類別：correctness；驗證：確認；來源：B6-backtest-engine
- 觸發條件：同一個 bar 的 matrix.entry 與 matrix.exit 都為真（open_t+1 時就是前一日同時有買點與賣點），minimum_holding_sessions 為預設的 1
- 影響：組合模式只在 minimum_holding_sessions == 0 時處理當根賣訊；其他情況不設 pending，訊號就消失，持有到下一個賣訊、到期或期末。全量模式則把它記成 pending，下一根賣出。重現：同一份訊號，position 模式 2024-01-06 期末 +40%，full 模式 2024-01-03 依訊號 +10%。使用者切換模式會得到矛盾的結論
- 建議修正：統一語意：組合模式在建倉當根遇到賣訊時也走 _try_sell，被最少持有擋下就記成 pending；或兩邊都忽略。另補一個跨模式一致性測試
- 驗證者意見：組合模式的出場迴圈（2014-2031）在建倉之前執行，建倉後只在 minimum_holding_sessions==0 時處理當根賣訊（2134-2147），訊號因此直接消失。獨立模式在 1421-1427 經 _try_close 設 pending，下一根賣出。matrix 與 strategy 都沒有把同根的 entry 與 exit 互斥。重跑 repro：position 期末 +40%，full 依訊號 +10%。前端預設 minimum_holding_sessions=1、mode=position，一般 UI 操作就會觸發，維持 P2。

### B016（P2）turnover_ratio_5d 與 turnover_z_60d 遇換手率空值時，矩陣路徑與 Polars 路徑結果不同

- 位置：`backend/app/backtest/matrix.py:4290`
- 區域：回測與挖掘；類別：consistency；驗證：確認；來源：B5-backtest-matrix
- 觸發條件：turnover_rate 中間出現空值時觸發。目前 2026-10-19、10-20 兩天全市場換手率都是空值（1,943 檔共 3,851 列），之後 5 個交易日內的 turnover_ratio_5d 與 60 個交易日內的 turnover_z_60d 都會觸發。
- 影響：矩陣路徑（matrix_native 選股、回測、factor_rank_research）會跳過空值日、用更早的有效日算出數值；Polars 路徑（mining 的 _compute_missing_factors → materialize_scoring_columns，以及 Polars 策略評分）因為 rolling_mean 的 min_samples 等於窗口長度，結果是 null。重現結果：兩個空值日之後的 4 天，矩陣路徑為 -0.04412、0.02941、0.10294、0.17647，Polars 全部是 null；turnover_z_60d 也一樣。mining 用 Polars 口徑算 IC 與覆蓋率，候選回測與選股卻用矩陣口徑，同一個因子有兩種定義。
- 建議修正：先定一種口徑：空值日當成缺列跳過，或遇空值就整窗為 null。兩條路徑改成同一種，並補一個含中間空值的雙路徑一致性測試。
- 驗證者意見：matrix.py:4290-4297 的 valid_shift 核心（:3110-3118）會跳過空值格，取更早的有效值。scoring.py:233-237 用 shift(1).rolling_mean(5)，本機 Polars 1.40.1 實測窗內只要有 null 結果就是 null。實際資料只有 7 個分區有 turnover_rate，10-19、10-20 全是空值，所以 10-21 的 turnover_ratio_5d 矩陣有值、Polars 是 null。mining_runtime.py:414 走的正是 Polars 路徑。turnover_z_60d 現在兩條路徑都還沒累積到 60 個有效值，因此目前沒有差異，宣稱這部分略為誇大。A09 稽核文件只寫到「無法驗證」，沒有記錄此問題。

### B017（P2）市場環境 T-1 對齊取的是矩陣上一個標籤，不是市場上一交易日；指定股票池時會用到過期的環境

- 位置：`backend/app/backtest/regime_alignment.py:102`
- 區域：回測與挖掘；類別：date-alignment；驗證：確認；來源：B5-backtest-matrix
- 觸發條件：回測指定股票池或單一標的（symbols 不是 None）、啟用 regime_filter，且標的有未成交日時觸發。enriched 只存有成交的列，實測 2,355 檔中有 617 檔中間缺日，例如 6236.TPEX 在 676 個交易日中只有 265 列。
- 影響：這種載入的時間軸只含所選標的有列的日期，所以「T-1」會取到好幾個交易日前的環境。重現：labels 為 10-12、10-13、10-16，環境在 10-14～10-16 都是 weak，10-16 的 T-1 卻取到 10-13 的 strong，states=['strong'] 的過濾因此放行進場。這不是未來函數，但流動性差的標的環境過濾失準，同一策略的全市場回測與單檔回測結果也會不同。
- 建議修正：改用市場交易日曆找前一交易日（分區日期清單，或 _matrix_time_axes 已用來算 session_ids 的日曆），再查該日的環境；不要用矩陣的相鄰標籤。
- 驗證者意見：regime_alignment.py:102-107 用 labels[index-1] 查環境。strategy.py:1281 把 first.symbols 傳給載入器；matrix.py:1113-1124 以帶 symbols 的 filter_expr 收集日期軸（_collect_parquet_axes :2168-2190），所以標籤只包含所選標的有列的日期。:1333 再把 timestamp_labels 傳給 _build_regime_mask。實測 2,355 檔中有 617 檔中間缺日，6236.TPEX 只有 265 列，對 678 個分區。單檔回測時 T-1 會取到好幾天前的環境。稽核文件 A11 只驗證了 T-1 的對齊方式，沒有記錄這個問題。

### B018（P2）config.py 的 AI 預設端點是第三方 api.zhaji.dev/gpt-5.5，與 .env.example 的 DeepSeek 預設矛盾

- 位置：`backend/app/config.py:91`
- 區域：核心、資料源與排程；類別：config；驗證：確認；來源：I1-infra-config-deps、B11-core-infra
- 觸發條件：只用環境變數或 .env 提供 AI_API_KEY，沒有提供 AI_BASE_URL（例如 `docker run -e AI_API_KEY=...`，或把 .env 的 AI_BASE_URL 行刪掉、註解掉），且 secrets.json 沒存過 ai_base_url
- 影響：_openai_client 以 secrets_store.get_ai_config("ai_base_url", settings.ai_base_url) 取得端點，此時會回退到程式內建的 https://api.zhaji.dev/v1 與模型 gpt-5.5。使用者以為照 .env.example 在用 DeepSeek，實際上金鑰與提示內容都送往一個未在任何文件說明的第三方中轉站；/api/settings 也會回報這個 URL。
- 建議修正：把 ai_base_url/ai_model 預設改為空字串，未設端點時 ai_configured() 回傳 False；或至少與 .env.example 一致並寫進文件。
- 驗證者意見：config.py:91 是 api.zhaji.dev，:93 是 gpt-5.5；.env.example:8-10 與 configuration.md 寫的是 DeepSeek。secrets_store.get_ai_config 依序取 secrets.json、settings、default；ai_provider.py:421 的 _openai_client 會回退到 zhaji。前端 AI.tsx:65 的 canSave 要求 baseUrl 非空，所以只有走環境變數、沒有提供 AI_BASE_URL 時才會觸發，與宣稱一致。repo 內只有 config.py 出現 zhaji。
- 同一問題的其他回報（B11-core-infra，評 P3，位置 `backend/app/config.py:91`）：AI base URL 預設指向第三方轉發網域；驗證者意見：config.py:91 預設為 https://api.zhaji.dev/v1，全 repo 只有這一處提到該網域，.env.example:8 範例是 deepseek。ai_provider.py:427 經 get_ai_config 回退到這個預設，所以只在 .env 設 AI_API_KEY 時，金鑰會送到 zhaji。反證：UI 存檔時 AI.tsx:65 的 canSave 要求填 baseUrl，UI 路徑安全；風險限於只設 .env 的情境，P3 合理。

### B019（P2）自訂即時源以中位數啟發式換算百分數，門檻是 A 股值

- 位置：`backend/app/data_providers/custom/provider.py:39`
- 區域：核心、資料源與排程；類別：data-caliber；驗證：確認；來源：B11-core-infra
- 觸發條件：使用者把即時行情來源設成自訂源，該源以百分數回傳 turnover_rate 或 change_pct，且整批絕對值的中位數不超過 0.31（例如台股換手率中位數常在 0.2~0.3%，或平盤日的漲跌幅）。
- 影響：這批資料不會被除以 100，會被當成小數制，數值放大 100 倍，經 quote_service.py:808 進入監控與統計。這也違反 CONTRIBUTING 3.1『禁止用數值大小啟發式轉換』的規定。0.31 是 A 股 30% 漲跌停的門檻，不適用台股。
- 建議修正：改由資料源設定明確宣告單位（transforms 已有 value / 100），移除啟發式；預設測試代碼改成台股代碼。
- 驗證者意見：provider.py:39、57-58 用 median/max > 0.31 決定要不要除以 100，CONTRIBUTING.md:96 明文禁止這類啟發式。docs/custom-data-source.md:128 又告訴使用者百分數會自動歸一，使用者會依賴它。我唯讀算了 data 中 2026-10-21 enriched 的百分數 turnover_rate，|值| 中位數是 0.146，低於 0.31。台股百分數制的換手率因此不會被轉換，經 quote_service.py:808 放大 100 倍。

### B020（P2）樞紐點以最後一根 K 計算，盤中會用到尚未完成的當日 K

- 位置：`backend/app/indicators/levels.py:190`
- 區域：策略、指標與監控；類別：correctness；驗證：確認；來源：B8b-indicators-builtin、K1a-audit-recheck、K1c-audit-sections
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md：G04-gap#1（RC21，P2，未修正）；recheck-2026-10-06.md G04-gap#1（RC21）；README P2 第 12 點；sections/G04-gap.md G04-gap#1（recheck 判未修正）
- 觸發條件：盤中呼叫 /api/stock-analysis/levels（end=今天，repository 會用 enriched_latest 覆蓋當日列）
- 影響：P/R1-3/S1-3 隨盤中價格持續移動，不是以前一個完整交易日計算的經典樞紐點，價位提醒的依據會漂移
- 建議修正：盤中改取最後一根已收盤的 K（日期小於當前交易日），或明確標示為盤中暫估
- 驗證者意見：levels.py:190 的 _pivot_points 取 df.tail(1)。stock_analysis.py 以 end=date.today() 呼叫 get_daily_asset；repository.get_daily 在今日分區尚未發佈時，會用 enriched_latest（盤中快照）補上最後一列，所以盤中樞紐點是用未完成的 K 計算。區段標題寫「基于最近完整交易日」，與實作矛盾；docstring 雖承認取最後一根，但沒有區分盤中與收盤後。已知條目 G04-gap#1，仍未修正。
- 同一問題的其他回報（K1a-audit-recheck，評 P2，位置 `backend/app/indicators/levels.py:190`）：盤中的樞軸點用未完成的當日 K 棒計算；驗證者意見：levels.py:190 取 df.tail(1)，但標題註解寫「基于最近完整交易日」。stock_analysis.py:120 的 end=today。repository.py:1644-1654 在沒有已發佈分區時，用 enriched_latest 覆蓋當日；台股盤中列只留在記憶體，不會寫盤(repository.py:2570-2575)。quote_service.py:1901 會把盤中列併入快取。觸發條件比宣稱窄：只限即時快取內的標的(自選或規則標的，且即時開關開啟)、交易時段內，但這正是設提醒的常見對象。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `backend/app/indicators/levels.py:190`）：即時行情開啟時樞軸點以當日未完成 K 棒計算，盤中持續漂移；驗證者意見：levels.py:190 的 _pivot_points 取 df.tail(1)。stock_analysis.py:118 以 end=date.today() 讀取資料。merge_live_enriched_asset（repository.py:2604-2605）不論是否寫盤，都會把當日即時列設成 _enriched_cache，get_daily（約 :1642-1652）在當日尚未發布時再用這一列取代同日資料。所以盤中自選標的的 P/R/S 是用未完成的 K 棒算的，數值會漂移。觸發前提是該標的在即時輪詢範圍內。

### B021（P2）缺口回補判定與 docstring 及通行定義不一致：部分回補即剔除、被跳空越過的缺口卻保留

- 位置：`backend/app/indicators/levels.py:461`
- 區域：策略、指標與監控；類別：correctness；驗證：確認；來源：K1c-audit-sections、B8b-indicators-builtin
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md：G04-gap#3（RC21，P2，未修正）；sections/G04-gap.md G04-gap#3（recheck 判未修正）
- 觸發條件：向上缺口形成後，後續某根 K 線 low 只觸及缺口上緣一點；或價格以另一個向下跳空整個越過缺口
- 影響：前者有效缺口被誤刪；後者已失效的缺口仍列為支撐／壓力，提醒價位錯誤
- 建議修正：採用單一定義（完全回補或觸及即回補）並同步 docstring；越過缺口也視為失效
- 驗證者意見：levels.py:461 寫 `lows[j] <= g_hi and highs[j] >= g_lo`，只要區間有任何交集就算回補。docstring :450-456 卻寫「僅觸及缺口邊緣（low 跌到上沿）不算回補」，而 low==g_hi 時程式會判成已回補，兩者直接矛盾。另外，如果後續以跳空整段越過缺口（high<g_lo），每一根都沒有交集，缺口會被保留下來繼續當作價位，和 docstring 的「價格穿越即回補」不符。
- 同一問題的其他回報（B8b-indicators-builtin，評 P3，位置 `backend/app/indicators/levels.py:461`）：缺口回補判定：任何一點交集就算回補，與 docstring「必須整個覆蓋、觸及邊緣不算」相反；驗證者意見：程式與內層 docstring 確實矛盾：:461 用區間相交判定回補，lows[j]==g_hi（觸及上沿）也算回補，但 :452-455 寫觸及邊緣不算。台股價格依升降單位跳動，剛好相等並不罕見。不過外層 docstring（:425-427）寫「只要回到缺口区间内就算已回补」，所以部分回補算回補可能是設計意圖，宣稱把這部分當錯誤是高估。只影響關鍵價位顯示這個局部功能，降為 P3。

### B022（P2）自訂訊號引用 distance_to_upper_limit／distance_to_lower_limit 時，在台股所有路徑都不會被計算

- 位置：`backend/app/indicators/pipeline.py:813`
- 區域：策略、指標與監控；類別：correctness；驗證：確認；來源：B8b-indicators-builtin
- 觸發條件：使用者在自訂訊號編輯器選「距漲停距離 < 0.02」這類條件（/api/signals/options 會提供這兩個欄位，custom_signals.validate 也接受）
- 影響：盤後發佈（compute_taiwan_enriched → compute_enriched → compute_signals）、台股盤中列（live_enriched）、回測重算都在距漲跌停欄位產生前就注入自訂訊號，因為依賴欄位缺少而整條跳過，只留一行 warning，csg_* 欄位不存在。這個自訂訊號在選股、監控、回測都不會觸發，使用者看不到錯誤。只有 A 股遺留路徑 compute_enriched_today 先加距離欄位（:1891）再注入（:1927）
- 建議修正：在 compute_all／compute_taiwan_enriched 注入自訂訊號前先呼叫 add_limit_distance_columns（需要時傳入 sessions），或在修好前把這兩個欄位從 ALLOWED_FIELDS 移除，並補一個自訂訊號引用距離欄位的測試
- 驗證者意見：親讀確認：compute_signals 在 pipeline.py:813 注入自訂訊號；距漲跌停欄位只由 add_limit_distance_columns 產生，呼叫點只有 :1891（compute_enriched_today）和 live_enriched.py:405（在 :434 重新注入之後）。compute_taiwan_enriched 與 repository/backtest 的重算路徑都不會先加這兩欄。我在 暫存目錄 實測 compute_enriched：log 出現 missing dependencies ['distance_to_upper_limit']，輸出沒有 csg 欄。/api/signals/options 直接回傳 ALLOWED_FIELDS，其中含這兩欄，所以使用者選得到，但訊號永遠不會觸發。

### B023（P2）未設密碼時『內網即放行』+ 預設 HOST=0.0.0.0,區網任何主機可無認證全權存取

- 位置：`backend/app/main.py:396`
- 區域：核心、資料源與排程；類別：security-authn；驗證：確認；來源：X2-security
- 觸發條件：伺服器以預設 HOST=0.0.0.0 啟動且尚未設過存取密碼時,auth_middleware 對 _is_local_network() 為真的來源一律放行。_is_local_network 把 10.x / 172.16-31.x / 192.168.x 全視為本機,因此與伺服器同區網的任何裝置在擁有者設定密碼之前,都能無認證呼叫全部 /api/ 端點。
- 影響：無認證讀取全部資料與設定,並可呼叫 /api/strategies/code/save 植入策略達成前述 RCE、改資料源(SSRF)、讀遮罩前的設定等。等於在設密碼前的整個窗口對整個區網裸奔。
- 建議修正：首次設密碼窗口僅放行 127.0.0.1/::1(真正本機),不要把整個 RFC1918 內網視為可信;或預設 HOST=127.0.0.1,公網/容器部署再顯式開放。文件應強調務必先在本機設好密碼再對外綁定。
- 驗證者意見：程式行為和宣稱一致。尚未設密碼時，main.py:396-399 會讓 _is_local_network 判為真的來源直接通過。auth.py:42-63 把 10/8、172.16/12、192.168/16 都當成本機，預設又綁定 0.0.0.0（config.py:110、dev.ps1:47、docker-compose.yml:13）。前端不會強制使用者設密碼（Layout.tsx:222、688 只拿 configured 決定要不要顯示登出），所以沒設密碼時整個區網都能長期無認證存取，不只是部署後的短暫空窗。策略 .py 由使用者自行撰寫，屬於設計功能，因此存檔端點在這個情況下等同讓區網內任何人執行任意程式。

反證方面：main.py:376-379 的註解寫明「未設密碼 + 本機/內網 → 放行」，conftest.py:72-80 也依賴這個行為，可見是刻意採用的區網信任模型。文件（deploy-password.md:85、configuration.md:83）只說明 setup 限本機或內網，沒寫出整個 API 都對內網開放。觸發需要攻擊者在同一區網，且擁有者一直沒設密碼。這比較像安全強化需求，不是意料外的漏洞；第 11 節 P1 的定義（核心流程、金融口徑）也不符合，所以降為 P2。既有審查文件裡沒有這一條。

### B024（P2）盤中即時合併在寫入判斷前就建立當日分區空目錄

- 位置：`backend/app/repository.py:2580`
- 區域：核心、資料源與排程；類別：correctness；驗證：確認；來源：B11-core-infra
- 觸發條件：交易日盤中開啟即時行情，merge_live_enriched_asset 收到台股自選列；這些列在 2569-2576 被過濾掉，write_to_disk 為 False，但 2580 已先執行 mkdir。
- 影響：kline_daily_enriched/date=<今天>/ 會留下一個沒有 part.parquet 的空目錄。latest_enriched_date（2237-2244）只看目錄名，因此回傳尚未發佈的今天，影響矩陣預熱與 mining 的預設結束日、資料指紋；api/data.py:163-166 的資料統計會把 latest_date 顯示成今天，trading_days 也多算一天。ETF／指數路徑則會讓 _refresh_etf_enriched／_refresh_index_enriched（1138-1148、1188-1198）去讀不存在的檔案，盤後 refresh 之後快取變空。若當晚發佈失敗，這個狀態會一直持續。
- 建議修正：把 mkdir 移到 if write_to_disk 區塊內；latest_enriched_date 與 ETF/指數 refresh 只採計含 part.parquet 的分區。
- 驗證者意見：repository.py:2580 的 mkdir 在 2581 的 if write_to_disk 之前。台股列在 2573-2576 被濾掉；ETF 在交易日盤中，_live_write_allowed（1544-1558）對未發佈的日期回傳 False，兩條路都會留下空目錄。quote_service.py:1901 在盤中走這條路。latest_enriched_date（2237-2244）和 data.py:163-166 只看目錄名。refresh_cache（566-570）先把 ETF/指數快取設成 None，之後 _refresh_etf_enriched 讀 part.parquet 失敗，例外被吞掉，快取維持 None。證據鏈完整。

### B025（P2）alert_store 以 ts 當唯一鍵，同輪告警 ts 相同導致刪錯紀錄與前端 key 衝突

- 位置：`backend/app/services/alert_store.py:126`
- 區域：後端服務；類別：correctness；驗證：確認；來源：B9b-services-app
- 觸發條件：同一輪監控評估一條規則命中多個標的（signal/price/market 規則可同時命中多檔），或多條規則同秒觸發；使用者在告警列表點刪除單筆。
- 影響：MonitorRuleEngine.evaluate 每輪只取一次 `now=time.time()`，該輪所有事件 ts 相同（int(now*1000)）。alert_store.delete_one 以 ts 比對且只刪第一筆同 ts（程式註解自承），所以 UI 刪某筆時實際刪掉的是同 ts 的第一筆（可能不是使用者點的那筆）。前端 TaiwanAlertsList 的 React key=`${rule_id}_${ts}`，同規則多標的事件 key 完全相同，造成 key 衝突與渲染/刪除行為錯亂。
- 建議修正：為每筆告警產生唯一 id（如 uuid 或 ts+rule_id+symbol+type）並以其為 delete_one 鍵與前端 key；或讓同輪事件 ts 以序號遞增。
- 驗證者意見：monitor.py:572 每輪只取一次 now，:881 讓同一條規則在同一輪命中的多檔標的共用同一個 ts。quote_service.py:1531 原樣寫進檔案。alert_store.py:148 只刪掉第一筆 ts 相符的紀錄，list_recent 是穩定排序，所以點第二筆時，被刪掉的其實是第一筆。前端 TaiwanAlertsList.tsx:88 的 key 是 rule_id_ts，事件也沒有 alert_id，同一輪的事件 key 會重複。審查文件沒有記載這一項。

### B026（P2）auth.json 非原子寫入 + 讀取損毀即靜默清空，會永久清掉密碼並使 LAN 面板變開放

- 位置：`backend/app/services/auth.py:61`
- 區域：後端服務；類別：security；驗證：可能成立；來源：B9b-services-app
- 觸發條件：任一次 _save 寫入過程中行程被中止（uvicorn --reload/斷電）導致 auth.json 截斷；之後任一 login/logout/會話過期清理觸發 _persist_sessions_locked。
- 影響：_save 直接 write_text（無 temp+os.replace），寫入中斷會留下半截 JSON。_load 對任何解析錯誤 except 吞掉回傳 {}。_persist_sessions_locked 以 `d=_load()` 讀出空 dict 後只寫回 `{'sessions':...}`，password_hash/password_salt 被永久覆蓋掉。結果：is_configured() 變 False → 擁有者被鎖在外且需重設密碼；更嚴重的是 auth_middleware 對『未設密碼』在內網/本機直接放行（main.py:396-408），面板在 LAN 變成無認證開放。
- 建議修正：_save 改 temp 檔 + os.replace 原子寫入；_persist_sessions_locked 不可在 _load 失敗（回 {}）時覆寫；_load 解析失敗應視為致命並保留原檔（備份）而非回空 dict。
- 驗證者意見：程式碼和宣稱一致：auth.py:63 用 write_text 寫入，先截斷再寫，不是原子操作；:55-58 解析失敗就回傳 {}；:197-201 會以空 dict 覆寫回去；main.py:396-399 在未設密碼時對內網直接放行，屬於 fail-open。不過觸發條件必須是寫入途中被強制終止或磁碟滿，機率無法確認。另外要修正一點：檔案一被截斷，hash 就已經沒了，is_configured 立刻變成 False，所以放行不需要等 persist 覆寫才發生。

### B027（P2）sync_daily_batch 的台股分支丟掉 count 與日期範圍，單股 live 後備只拿到當月 K 線；選了自訂 daily 源則固定回 502

- 位置：`backend/app/services/kline_sync.py:128`
- 區域：後端服務；類別：data-contract；驗證：確認；來源：B9a-services-data、R1-redundant-docs-assets、T1-backend-static
- 觸發條件：GET /api/kline/daily?symbol=X，而 X 在 enriched 和本地 taiwan/daily 都沒有資料（例如新上市或尚未被每日更新收錄的標的、本地讀取失敗）。此時 kline.py:607-609 會呼叫 sync_daily_batch([symbol], count=days+30)。另一個觸發點：在資料源頁把日 K 改成自訂來源。
- 影響：台股分支只把 start_time/end_time（兩者都是 None）轉給 provider.get_daily，count 被丟棄。OfficialTaiwanAdapter.fetch_daily 在 start=None 時只抓當月（official_provider.py:137-143），所以 source='live' 的 K 線只有本月 1～22 根（月初只有 1～2 根），遠少於 days（預設 120），均線等指標也算不出來。official 失敗改走 FinMind/Yahoo 時又變成 365 天，結果取決於哪個來源成功。kline.py:619-620 在能力全開時呼叫 fetch_adj_factor_single，但它在台股模式一律回空（:886-898），因此 live 路徑回的是未復權原始價，和 enriched / taiwan_local 的前復權口徑不一致。若 daily provider 是自訂來源，:125 條件不成立，:130 的 get_client() 必定拋 RuntimeError（tickflow/client.py:20-21），kline.py:610-611 回 502，自訂源完全沒被使用（違反 CONTRIBUTING 第 4 節）。
- 建議修正：台股分支依 count 換算 start_time（交易日轉自然日並留安全量），或讓呼叫端直接傳 start/end；自訂來源改走 custom_sources.get_provider；live 路徑改用台股公司行動因子（adjustment_factors）做前復權。
- 驗證者意見：缺陷在 :128：只轉傳 start_time/end_time（kline.py:609 只給 count，兩者都是 None）。official fetch_daily 在 start=None 時 earliest=cursor，只抓當月（:141-143）；FinMind 則預設 365 天。fetch_adj_factor_single 在 taiwan 模式回空（:889-890），所以 live 路徑是未復權價。自訂 daily 源會讓 :130 的 get_client 拋例外，被 kline.py:610 轉成 502。但這只在 enriched 與本地都空時才觸發（:607），例如新安裝，或 dateRange 早於本地覆蓋範圍。宣稱的第二觸發點漏了這個前提。
- 同一問題的其他回報（T1-backend-static，評 P3，位置 `backend/app/services/kline_sync.py:130`）：非台灣官方日 K 來源的回退分支呼叫必定拋例外的 get_client()，單股日 K 線上補抓一律回 502；驗證者意見：kline_sync.py:125-128 只有 taiwan/tickflow 會走 registry，130 行無條件呼叫 get_client()。tickflow/client.py:20-21 會直接 raise RuntimeError，我用 python 實測確認。api/kline.py:607-611 在 df 為空時呼叫 sync_daily_batch，失敗後包成 502。sync_daily_full（kline_sync.py:222-246）有自訂源分支，單股補抓這條路徑沒有，宣稱成立。要觸發必須同時滿足兩個條件：設定了自訂日 K 源，且 enriched 與本地日線都沒有該標的。影響範圍窄，維持 P3。
- 同一問題的其他回報（R1-redundant-docs-assets，評 P3，位置 `backend/app/services/kline_sync.py:130`）：自訂日K來源時，sync_daily_batch 會呼叫已停用的 TickFlow client，個股日K回 502；驗證者意見：kline_sync.py:125-128 只對 taiwan/tickflow 走 registry，其他名稱直接到 :130 get_client()，client.py:20-21 一律 raise。preferences.py:260-264 允許自訂來源名稱；前端 DataSources.tsx:367-371 在來源宣告 daily 時會寫入該名稱。kline.py:595-611：enriched 與本機台股日K都沒有時呼叫 sync_daily_batch，例外轉成 502「TickFlow fetch failed」。觸發路徑成立。範圍窄（只影響不在 enriched 的標的），維持 P3。

### B028（P2）台股模式下分時訊號監控回報「可用」，但取數一律回傳空表，規則永遠不會觸發

- 位置：`backend/app/services/kline_sync.py:754`
- 區域：後端服務；類別：correctness；驗證：確認；來源：B9a-services-data
- 觸發條件：使用預設的 minute_data_provider='taiwan'，在監控中心建立含 signal_intraday_avg_cross_up / zero_cross 等分時訊號（op=truth）的規則並指定標的。
- 影響：tickflow/policy.py:12-28 在台股模式授予全部能力，包含 INTRADAY_BATCH 與 KLINE_MINUTE_BATCH。intraday_monitor_support 因此回傳 available=True、source='intraday_batch'、max_symbols=100，/api/monitor-rules/options 顯示可用，儲存閘門（monitor_rules.py:329-336）也放行。但 fetch_intraday_monitor_batch 遇到 taiwan provider 在 :812-813 直接回傳空表，IntradaySignalEvaluator.evaluate 拿到空表就回 []（strategy/intraday_signals.py:65-66）。結果是規則永遠不會觸發，使用者卻以為已經布防，違反 CONTRIBUTING 5.2「無權限時給出明確狀態」。
- 建議修正：provider 是 taiwan/tickflow 且沒有可解析的 custom minute 源時，intraday_monitor_support 應回傳 available=False 並附明確原因；或改讀台股輪詢在本地累積的 kline_minute。
- 驗證者意見：有讀程式碼，也實測過。預設 provider 是 taiwan，_resolve_minute_provider 因沒有 custom 源而走 fallback；policy.py:21 授予 INTRADAY_BATCH，所以 :754-764 回傳 available=True、source=intraday_batch。暫存目錄 用 detect_capabilities() 實測得到同樣結果。fetch_intraday_monitor_batch 在 :812-813 回空表，evaluate 在 :66 回 []。monitor_rules.py:210 的選項與 :329-336 的儲存閘門都放行，規則永遠不會觸發。測試只覆蓋 minute_batch 與 custom 源。

### B029（P2）台股模式的分時穿越訊號規則可以存檔，也回報「可用」，但取數永遠為空，規則永不觸發

- 位置：`backend/app/services/kline_sync.py:812`
- 區域：後端服務；類別：capability；驗證：確認；來源：B8a-strategy-monitor
- 觸發條件：台股預設設定（minute_data_provider=taiwan）下建立含 signal_intraday_avg_cross_up/down 或 signal_intraday_zero_cross_up/down 的監控規則（scope=symbols）。
- 影響：規則存檔成功，列表也沒有 runtime_warning，但盤中永遠不觸發。使用者會誤以為行情沒穿越。這違反 CONTRIBUTING §5.2「無權限時給出明確狀態」。
- 建議修正：台股模式的 intraday_monitor_support 應回 available=False 並附明確原因，或接上 taiwan minute_recorder 的本機分鐘線。均價改用 volume_shares_expr 口徑，不要寫死 *100。
- 驗證者意見：policy.py:20-21 授予 INTRADAY_BATCH。plugins 目錄是空的，沒有 taiwan 分鐘插件，_resolve_minute_provider 因此回傳 fallback。實測 support={'available':True,'source':'intraday_batch'}，fetch 回傳 (0,0)，原因是 kline_sync.py:812 遇到 taiwan 直接回空表。save_rule 與 list_rules 只看 available；前端 RuleEditor 也顯示可監聽 100 檔，規則永遠不會觸發。

### B030（P2）Windows 系統通知經 winotify 將告警文字注入可展開的 PowerShell here-string,造成命令注入

- 位置：`backend/app/services/notify_adapter.py:112`
- 區域：後端服務；類別：security-injection；驗證：確認；來源：X2-security
- 觸發條件：系統通知開啟且在 Windows 桌面時,告警 body 由 symbol/name/message 組成(quote_service.py:1816-1824)後交給 winotify。winotify 1.1.0 把 title/msg 以 str.format 直接塞進其 TEMPLATE 的雙引號 here-string($Template = @"..."@)中的 <![CDATA[...]]>,再用 powershell.exe -Command 執行。PowerShell 的 CDATA 不阻止 here-string 內的 $(...) 子運算式展開,故文字中的 $(...) 會被當 PowerShell 執行。
- 影響：任一條會觸發系統通知且 name/message 含 $(...) 的告警,會在擁有者的 Windows 機器上以後端行程權限執行任意 PowerShell。name 可能來自資料源(例如惡意自訂資料源回傳的個股名稱),message 可由監控規則設定。
- 建議修正：升級/替換 winotify 為會對 XML/PowerShell 內容做跳脫的實作,或在 notify_adapter 送出前移除/轉義 $、反引號、$( 等 PowerShell 可展開字元(至少對 name/message 做白名單過濾)。
- 驗證者意見：實際匯入的是 site-packages/winotify/__init__.py（1.1.0）。:28-42 用的是會展開的 here-string，:195 用 str.format 填入 title/msg 後以 -Command 執行，沒有跳脫，notify_adapter.py:112-117 也沒處理。我在 暫存目錄 用同樣結構的模板重現，msg 內的 $(...) 成功寫出 marker 檔。monitor_rules.py:137 的 message 是未驗證的 str，可讓有 API 權限者升級成在 OS 上執行指令。前提是 Windows 且系統通知開啟（預設關閉，preferences.py:669），所以維持 P2。

### B031（P2）欄位設定每次變更都立刻 PUT，而後端 preferences.save 沒有鎖也不是原子寫入，連續操作可能寫回舊設定或清空其他偏好

- 位置：`backend/app/services/preferences.py:57`
- 區域：後端服務；類別：併發／持久化；驗證：可能成立；來源：GAP3-user-customization、B9a-services-data、X3-concurrency-cache-time
- 觸發條件：在自訂欄位面板快速連續勾選，或在日 K／分時的寬高輸入框逐鍵輸入。這些輸入的 onChange 與 onBlur 每次都觸發 onChange（ListColumnCustomizer.tsx:572-582、627-634），經 Watchlist.tsx:729-732 進入 saveColumnConfig 發 PUT，使多個 PUT /api/settings/preferences/watchlist-columns 在 threadpool 中並行。
- 影響：一、兩個請求的讀、改、寫交錯時，較舊的欄位設定可能最後才寫入磁碟；下次載入以後端為準（watchlist-columns.ts:130-137），使用者最後的變更被還原。二、若某個請求的 load() 正好讀到另一個請求 write_text 截斷後、尚未寫完的檔案，load 回傳 {}（preferences.py:49-51），save 以 {} 合併後寫回，preferences.json 只剩 watchlist_columns，即時行情開關、側欄與導覽等偏好全部遺失。
- 建議修正：preferences.save 加 threading.Lock，改為寫暫存檔再 os.replace。load 解析失敗時應拋錯，不可作為 save 的基底。前端對 saveColumnConfig 去抖，並把請求串成序列。
- 驗證者意見：preferences.py:57-65 依序 load、update、write_text，沒有鎖也不是原子寫入；:49-51 解析失敗回 {}。settings.py:754 的同步端點在 threadpool 並行；Watchlist.tsx:729-732 每次變更就送 PUT。邏輯成立，但時窗只有毫秒級且未重現。一旦發生會遺失資料源路由偏好。
- 同一問題的其他回報（X3-concurrency-cache-time，評 P3，位置 `backend/app/services/preferences.py:59`）：preferences.json 的讀改寫沒有鎖也不是原子寫入，輪詢執行緒每 30 秒寫一次，會吃掉使用者設定，最壞會清空整份偏好；驗證者意見：preferences.py:57-65、114-122 的讀改寫確實無鎖，且用 write_text 非原子寫入；讀檔失敗時 49-51 回 {}，save 會拿 {} 當基底寫回。輪詢執行緒每 30 秒呼叫一次 save。反證：load 大多命中 mtime 快取，丟更新與撕裂讀都要剛好落在約 1ms 的窗口，機率很低，未實際重現，降為 P3。
- 同一問題的其他回報（B9a-services-data，評 P2，位置 `backend/app/services/preferences.py:61`）：preferences.save 沒有加鎖、也不是原子寫入：並行儲存會蓋掉使用者剛改的設定，甚至把整份 preferences.json 清空；驗證者意見：宣稱的機制成立。_poll_loop 背景執行緒每 30 秒會經 _persist_last_fetch 呼叫 preferences.save。settings.py 的設定端點都是同步 def，會在 threadpool 中與它並行。save 是讀改寫，沒有加鎖，write_text 會先截斷再寫入；load 解析失敗時回傳 {}，save 再以 {} 為基底寫回。本機重現時單次 save 約 22ms，整份 preferences.json 確實會被清空。不過正式環境需要使用者存設定時，剛好落在每 30 秒一次、約數十毫秒的寫入窗口內，或後端行程在寫入途中被終止，實際機率很低。目前使用者的檔案仍完整。被清空的是設定，不是行情或交易資料，所以從 P1 下修為 P2。另外，宣稱列出的 set_financial_sync_time 在 app 內沒有呼叫端，不算實際寫入者。

### B032（P2）輪詢執行緒每 30 秒寫一次 preferences.json，與 API 執行緒的讀寫沒有鎖也不是原子操作，可能整份偏好被清空

- 位置：`backend/app/services/quote_service.py:165`
- 區域：即時行情；類別：concurrency；驗證：確認；來源：B4-realtime-universe-rules
- 既有文件：未見於既有審查文件
- 觸發條件：輪詢執行緒的 _persist_last_fetch 在 _process_full_market_records 內呼叫 preferences.save（:987），每 30 秒一次。同一時間另一個執行緒也在讀寫：例如使用者在設定頁存檔、切換即時開關（_save_enabled），或其他 API 呼叫 preferences.save。
- 影響：preferences.save 是「load → update → write_text」，沒有鎖，write_text 會先把檔案截斷再寫入。另一個執行緒在截斷到寫完之間 load() 的話，JSON 解析失敗，load() 回傳 {}（preferences.py:49-51）。如果那個執行緒正在 save，就會把整份約 10.5KB 的設定覆寫成只剩這次更新的一兩個鍵：自選欄位配置、即時開關、資料來源、監控 ext 欄位等全部遺失，而且不會報錯。如果是一般讀取，該次 getter 會拿到預設值。
- 建議修正：preferences.save 加行程內鎖，改成寫暫存檔再 os.replace；load() 讀到半份檔案時重試，不要把 {} 當成目前內容去合併寫回。
- 驗證者意見：preferences.save 是 load→update→write_text，沒有鎖也不是原子寫入；write_text 截斷檔案的瞬間，另一執行緒 load() 會解析失敗並回傳 {}（:49-51），接著 save 就把整份覆寫成少數幾個鍵。輪詢執行緒（:165、:1429）與 sync API 的 threadpool 確實會並行執行。我在 暫存目錄 用真實模組做高頻壓力測試：empty_loads=65，最終檔案只剩 2 個鍵。正式環境的時間窗很短，屬低機率但後果嚴重，違反 §6.2。

### B033（P2）即時行情在自訂資料源缺 realtime 資料集時落入已刪除的 TickFlow 分支，`tf` 未定義導致 NameError，整輪行情停擺

- 位置：`backend/app/services/quote_service.py:848`
- 區域：即時行情；類別：correctness；驗證：確認；來源：T1-backend-static、B4-realtime-universe-rules
- 既有文件：未見於既有審查文件
- 觸發條件：realtime_data_provider 指向一個已載入、但沒有 realtime 資料集的自訂資料源。有兩條可達路徑：(1) 先在設定頁選了某個自訂源（前端 DataSources.tsx:363-372 只在該源支援時才把 realtime 指給它），之後經 POST /api/settings/data-sources（settings.py:615 save_data_source）把該源的 realtime 資料集移除。save 路徑不會回退偏好，delete（settings.py:645-646）和 uninstall（settings.py:589-596）則會回退。(2) 直接呼叫 PUT /api/settings/preferences/data-providers（settings.py:685），它不檢查資料集就寫入偏好。
- 影響：_fetch_full_market_quotes 每個輪詢週期都在 848 行拋 NameError: name 'tf' is not defined。例外被 855-857 行的 except 吞掉，只記一行「行情拉取失败」warning 就 return。因為 provider_name 不是 taiwan，台灣官方 MIS 路徑（765 行起）整段被跳過，自選股、監控規則與全市場即時報價全部停止更新，畫面上看不出原因。
- 建議修正：provider 既不是 taiwan、也不是有 realtime 資料集的自訂源時，直接改走 765 行的台灣官方 MIS 路徑（或先把 provider_name 正規化成 taiwan），並刪除 836-853 行殘留的 TickFlow universes 分支。save_data_source 移除資料集時也應比照 delete_data_source 回退偏好。
- 驗證者意見：quote_service.py 整檔 grep `\btf\b` 只在 848、853 與另一函式 _fetch_watchlist_quotes 的 1056 行出現，模組層沒有 tf，_fetch_full_market_quotes 內也沒有賦值。realtime_mode() 固定回 "full_market"（488-490），所以這個函式一定會執行。preferences.py:281-285 會原樣保留已載入的自訂源名稱。save_data_source（settings.py:614-625）與 save_config 都不會回退偏好，PUT data-providers 也不檢查資料集。realtime_pull_stock 預設是 True（preferences.py:590），universes 不為空，848 行必定拋 NameError，被 855 行吞掉後直接 return，報價停止更新。
- 同一問題的其他回報（B4-realtime-universe-rules，評 P3，位置 `backend/app/services/quote_service.py:848`）：自訂 provider 沒有 realtime 資料集時落入 TickFlow 舊路徑，tf 未定義，每輪都拋 NameError；驗證者意見：ruff --select F821 確認 :848、:853 的 tf 未定義，檔內唯一的 tf 是 :1056 的區域變數。update_data_providers（settings.py:685-690）不驗證所選 provider 是否具備 realtime 資料集；get_realtime_data_provider 會把已載入的自訂源名稱原樣回傳。loader.py:308 的註解寫明「未啟用就回退 TickFlow」，但 TickFlow 已經移除，所以每輪都會 NameError，並在 :855 被吞成 warning。

### B034（P2）自選截圖 OCR 只抓 6 位代碼，台股 4/5 位代碼全數漏抓，且比對讀空的 A 股 instruments

- 位置：`backend/app/services/watchlist_ocr/pipeline.py:17`
- 區域：後端服務；類別：correctness；驗證：確認；來源：B9b-services-app、F6-components-shared、X1b-dead-endpoints
- 觸發條件：台股使用者在 /api/watchlist/import-image 上傳自選截圖（內含 2330、0050、00878、00631L 等台股/ETF 代碼）。
- 影響：_CODE_RE=`(?<!\d)(\d{6})(?!\d)` 只匹配恰好 6 位數字（A 股/滬深代碼慣例）。台股上市股票為 4 位（2330、2317…）完全不匹配，多數 ETF 為 5 位（00878）也不匹配，只有 6 位（006208）會中。build_instrument_lookups 的 code 亦硬性要求 len==6。再者 lookup 只讀 data/instruments/instruments.parquet 與 data/instruments_etf/*.parquet（台股安裝下皆為空目錄），不查台股 security master，因此即使抓到代碼也無法解析成 symbol。整個截圖匯入功能對台股主要市場不可用——A 股遺留邏輯。
- 建議修正：改用台股代碼規則（4–6 位數字，含 00631L 這類帶字母尾碼），並讓 build_instrument_lookups 改查台股 security master（app.taiwan.universe）而非空的 instruments 目錄。
- 驗證者意見：pipeline.py:17 的正則只吃 6 位數，:82-87 也要求 len==6，:61-66 只讀 instruments/instruments_etf，而這兩個目錄實際是空的。我用 venv 實跑 extract_codes，台股樣本只回 ['006208']，漏掉 2330、2317、0050、00878、00631L。前端 Watchlist.tsx:1335/1973 有實際入口，所以台股使用者用不了這個功能。審查文件沒有記載這一項。
- 同一問題的其他回報（F6-components-shared，評 P2，位置 `backend/app/services/watchlist_ocr/pipeline.py:17`）：截圖匯入自選在台股模式下完全無法比對：只抽六位數代號且查 A 股主檔；驗證者意見：pipeline.py:17 只抽六位數（拆分修補 :41 也只拼回六位），四位數個股與五位數 ETF 一律抽不到。:62 只讀 data/instruments/instruments.parquet 與 instruments_etf；這兩個目錄實測都是空的，api/watchlist.py:177 傳入的是 store.data_dir，沒有任何台股主檔備援，所以六位數代號也比對不到。WatchlistImportDialog.tsx:182-191 會依結果跳出上述錯誤訊息。功能入口在 Watchlist.tsx:1332，可以點，但實際上完全不能用，判 P2 成立。
- 同一問題的其他回報（X1b-dead-endpoints，評 P2，位置 `backend/app/services/watchlist_ocr/pipeline.py:17`）：自選「從截圖匯入」只辨識 A 股六位代碼與 A 股 instruments，台股截圖永遠配對 0 檔；驗證者意見：pipeline.py:17 的 _CODE_RE 只抓六位數字，:18 的拆分合併也只拼回六位。用同一組 regex 跑台股樣本（2330、0050、00878、006208、2317），結果只抽出 006208。build_instrument_lookups（:56-95）只讀 data/instruments 和 instruments_etf，這兩個目錄實際是空的；repository.py:2363 的 save_etf_instruments 沒有任何呼叫者，instrument_sync.py 原始檔也已刪除。前端按鈕（Watchlist.tsx:1332-1345）只檢查 OCR 是否可用，沒有其他限制。所以 matched_count 一定是 0，整個功能無法使用。docs 搜不到相關記錄。

### B035（P2）Telegram Bot token 透過 httpx INFO 日誌明文寫入 data/backend.log

- 位置：`backend/app/services/webhook_adapter.py:57`
- 區域：後端服務；類別：security；驗證：確認；來源：B9b-services-app
- 觸發條件：使用者設定 Telegram 推送並觸發告警，或在設定頁按『測試通知』(POST /api/settings/preferences/telegram-bot/test)。
- 影響：send_telegram 把 token 放進 URL `{TELEGRAM_API_ROOT}/bot{token}/sendMessage`。httpx 的 logger 以 INFO 記錄 `HTTP Request: POST .../bot<TOKEN>/sendMessage`，而專案 log_level 預設 INFO 並以 RotatingFileHandler 落盤 data/backend.log。因此 bot token 每次推送都明文寫入日誌檔。模組 docstring 與 except 分支特意不印 token，卻被 httpx 的 URL 日誌繞過洩漏。LINE 走 Authorization header 不受影響，僅 Telegram 中招。違反 CONTRIBUTING §8『日誌不得包含授權信息』。
- 建議修正：把 `logging.getLogger('httpx').setLevel(logging.WARNING)`（或啟動時關閉 httpx request 日誌），並避免把密鑰放 URL 無法時至少確保日誌不記 query/path 中的 token。
- 驗證者意見：webhook_adapter.py:57 把 token 放進 URL 路徑；main.py:54-57 以 settings.log_level（config.py:112 預設 INFO）設 root logger，:65-78 加 RotatingFileHandler 寫 data/backend.log；全 repo 找不到任何 setLevel 壓低 httpx 的程式。data/backend.log 現有 1106 行 `httpx: HTTP Request: GET https://...`，證明 httpx 會把完整 URL 寫進檔案。settings.py:1111 的測試端點與 quote_service.py:1784 的告警推送都會走到這裡。目前 log 裡沒有 telegram 紀錄，是因為使用者還沒用過這個通道，不影響結論。審查文件沒有記載這一項。

### B036（P2）以前復權後的絕對門檻過濾歷史 K（min_volume、price_min），會用到未來的公司行動

- 位置：`backend/app/strategy/builtin/pullback_resume.py:96`
- 區域：策略、指標與監控；類別：look-ahead；驗證：可能成立；來源：B8b-indicators-builtin
- 觸發條件：回測區間內的標的之後發生減資、面額變更或分割（volume_factor≠1），且策略存有 min_volume>0 的覆寫（swing_trend_confirmation 同理）；或預設基本過濾 price_min=3 遇到之後有大額現金股利或減資的低價股
- 影響：過去某天的成交量會乘上「之後」才發生的股數事件因子（pipeline.py:437-439），價格會被之後的股利或減資縮放。某天能不能通過門檻，取決於當時還不知道的資訊，屬於 CONTRIBUTING 5.3 禁止的未來函數。目前影響面窄：min_volume 預設 0，只有存檔的舊覆寫會觸發；price_min 只影響 3 元附近的股票，所以判為 P2 而不是 P1
- 建議修正：絕對門檻改用原始尺度：成交量以 volume / 量因子還原，價格用 raw_close；或移除 min_volume 覆寫的相容處理
- 驗證者意見：邏輯成立：matrix.py:4025-4026 用前復權的 market.close 比 price_min=3（engine.py:47 的預設，所有策略都會套用）；成交量會因之後的股數事件被 _apply_adj_factor（:437-439）縮放。歷史某日能否入選，取決於之後才發生的公司行動。但錨點行 :96 的 min_volume 預設為 0，data/user_data 也沒有 min_volume 覆寫，這部分目前不會觸發；price_min 部分沒有用資料量化實際影響，所以判 PLAUSIBLE。

### B037（P2）盤中矩陣快取跨交易日沿用，不因 enriched 重新發佈而失效

- 位置：`backend/app/strategy/engine.py:927`
- 區域：策略、指標與監控；類別：cache-consistency；驗證：可能成立；來源：B8a-strategy-monitor
- 觸發條件：後端連續運行跨過交易日，次日監控首輪的 current 標的集合、欄位與暖機根數都和前一日相同（fingerprint 相同）。期間 enriched 已在盤後重新發佈（官方量、換手率、除權息前復權重算，或 --full 全量重發）。
- 影響：monitor:stock 快取只會 append 新的盤中列。前一日的盤中快照列會一直留在歷史，不會被盤後發佈的官方列取代；重新發佈的歷史修正也不會進入盤中監控。matrix_native 策略（目前全部內建策略）的盤中訊號與選股池，可能與盤後 /run 的結果不一致。
- 建議修正：fingerprint 加入 enriched generation/version 或歷史最後交易日；交易日切換或 enriched 發佈完成時清除 _realtime_matrices。
- 驗證者意見：fingerprint 只有欄位、暖機根數與 symbols（918-922），命中時呼叫 buffer.update，新日期會 append（matrix.py:2725-2729）。_realtime_matrices 只在 reload 與 unregister 時清除，grep 結果顯示沒有每日或發佈時的失效機制；測試也確認前一日的盤中列會留在 buffer。除權息日的前復權重算也不會反映。但「跨日連續運行且標的集合不變」這個觸發條件無法完全確認。

### B038（P2）編輯監控規則的條件後，邊緣觸發與冷卻狀態沒有重置，新條件不觸發

- 位置：`backend/app/strategy/monitor.py:399`
- 區域：策略、指標與監控；類別：state-consistency；驗證：確認；來源：B8a-strategy-monitor
- 觸發條件：price、signal 或 market 規則已觸發過一次（edge 模式 armed=False，_last_fire 已記錄）。使用者在監控頁修改 conditions 的值、運算子或欄位，或修改 logic，然後存檔（API 呼叫 _sync_engine → set_rules）。
- 影響：修改後的規則即使當下條件成立也不觸發：edge 模式要等條件先轉假再轉真；level 模式要等舊規則的 cooldown（預設 3600 秒）過完。使用者以為規則已生效，實際上會漏掉告警與 LINE/Telegram 推播。
- 建議修正：把 conditions（含 rearm_value）、logic、trigger_mode、group_id 正規化後加入 _rule_state_signature，並補「改條件後立即重新觸發」的回歸測試。
- 驗證者意見：monitor.py:399-416 的簽名不含 conditions、logic、trigger_mode、group_id。set_rules（428-462）只清除簽名有變的規則狀態，api/monitor_rules.py:73-82 存檔時會走 set_rules。我在 暫存目錄 實測：edge 模式在 close>10 觸發後改成 close<20，觸發數為 0；level 模式（cooldown 3600）做同樣修改，觸發數也是 0。現有測試只涵蓋 symbols 變更。

### B039（P2）策略規則的批次事件以事件類型作冷卻鍵，冷卻期內新進池的其他標的通知永久遺失

- 位置：`backend/app/strategy/monitor.py:861`
- 區域：策略、指標與監控；類別：monitor-dedup；驗證：確認；來源：B8a-strategy-monitor
- 觸發條件：type=strategy 規則，notify_events 含 pool_entry、pool_exit、buy_signal 或 sell_signal，cooldown 為預設 3600 秒。某一輪有超過 5 檔同類變更，已送出一則批次事件。冷卻期內另一輪又有超過 5 檔「不同標的」進池或出池。
- 影響：第二批完全不通知。_strategy_pools 已經先更新成新池，這些標的之後也不會再被判為變更，所以通知永遠遺失。全市場範圍的策略監控在開盤後最容易遇到。
- 建議修正：批次事件依各標的的 (rule, symbol, ev_type) 冷卻過濾後再合併；或批次冷卻鍵改含標的集合的雜湊。被冷卻擋下的變更不應先寫進 _strategy_pools。
- 驗證者意見：批次事件的冷卻鍵是 (rule,'_{ev}_batch',ev)，不含標的（861-863）。池狀態在產生事件前已在 1098 覆寫，超過 5 檔時合併（1147）。我用假策略引擎實測：A0-A5 進池時送出 1 則批次；接著 B0-B5 進池，第三、四輪事件都是 []，這些標的之後也不會再被判為變更，通知永久遺失。

### B040（P2）異常診斷的 5 日量額基期把無成交或停止交易的列（OHLC 為 null）當成有效交易日

- 位置：`backend/app/taiwan/abnormal_diagnostics.py:322`
- 區域：台股資料與分析；類別：data-definition；驗證：確認；來源：B3-taiwan-analysis
- 既有文件：沒有被記錄。A02-ma-macd#7 與 A04-boll-atr-vol#9 只處理了自選頁與選股器的停牌列（recheck-2026-10-06.md:106、:124）
- 觸發條件：目標日前 5 個交易日內，個股有停止交易或無成交的日子（日K分區把這些日子存成 OHLC null、volume 為 0 或 1 股），目標日則恢復正常成交。
- 影響：0 量拉低平均量，VOLUME_SPIKE 與 TURNOVER_SPIKE 因此誤報，而且常被判為 extreme。診斷的 volume_ratio_5d 與選股頁讀的 enriched vol_ratio_5d 同名不同義：後者經 filter_halt_days，只算有成交日。9/9～10/21 每個交易日都有 8～28 筆這類訊號。另外，復牌當日的前一日若是無成交列，prev_close 會是 null，PRICE_MOVE 也就不評估。
- 建議修正：基期只用 OHLC 非 null 且 volume>0 的列（或直接讀 enriched 的 vol_ratio_5d），把條件改成「5 個有成交日」。前收改取最後一個有成交日，或改用 enriched 的 prev_close。
- 驗證者意見：:287 讀出的基期沒有經過 filter_halt_days。:322-326 的 mean 與 count 都把 volume=0 計入，:422、:443 只要求 count==5 且 mean>0。enriched 在 pipeline.py:1186 先 filter_halt_days（OHLC 為 null 的列會被濾掉）。實讀 8923.TPEX：基期 10/14～10/20 的量為 1、0、1、0、6002，10/21 的量 12042，比值 10.03，與宣稱一致。:394 前收取自前一日列，該列為 null 時 PRICE_MOVE 不評估，也成立。

### B041（P2）融資或融券 20 日中位數為 0 時，MARGIN_SURGE／SHORT_SURGE 直接不評估

- 位置：`backend/app/taiwan/abnormal_diagnostics.py:534`
- 區域：台股資料與分析；類別：correctness；驗證：確認；來源：B3-taiwan-analysis、K1c-audit-sections
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md:233 G01-gap#3（未修正）；sections/G01-gap.md G01-gap#3（recheck 判未修正）
- 觸發條件：平日幾乎沒有融資（券）變動、前 20 日多數為 0 的標的，某日突然大增。
- 影響：最極端的激增反而不會觸發；docstring :20-23 也沒有寫出這個條件。
- 建議修正：中位數為 0 時，改用絕對門檻或以 MAD 加上下限，並寫進 docstring 與前端說明。
- 驗證者意見：:533-534 與 :553-554 要求中位數 >0，docstring :20-23 沒有寫這個條件。用實際融資分區（136 日）模擬同一條件：前 20 日中位數為 0 且當日達門檻，融資有 27 次、融券有 180 次被靜默跳過，包含 9136.TWSE、5464.TPEX、1718.TWSE 等股票，不只 ETF，觸發條件確實會發生。與 G01-gap#3 一致，仍未修正。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `backend/app/taiwan/abnormal_diagnostics.py:534`）：融資／融券 20 日中位數為 0 時直接不評估，漏掉最極端的激增；驗證者意見：abnormal_diagnostics.py:533-534 與 :553-554 都多了 med_abs>0 的前置條件，docstring :20-23 沒有寫這個條件，也沒有任何 unevaluated 揭露。冷門標的（特別是融券）前 20 日中位數為 0、當日卻暴增並達到絕對門檻時，會被靜默略過，形成「越極端越不報」的非單調行為。G01 稽核以實際資料重現出 55 筆漏報。維持 P2。

### B042（P2）融資融券 20 日中位數為 0 時，異常診斷不評估也不提示

- 位置：`backend/app/taiwan/abnormal_diagnostics.py:554`
- 區域：台股資料與分析；類別：correctness；驗證：確認；來源：K1a-audit-recheck
- 既有文件：recheck-2026-10-06.md G01-gap#3（RC26）；README P2 第 12 點
- 觸發條件：某檔股票近 20 日的融券（或融資）變動大多是 0（中位數為 0），當天突然變動 1000 張
- 影響：med_abs_s20 > 0（或 med_abs_m20 > 0）不成立就整段跳過，最需要警示的「從無到有」爆量反而永遠不會觸發，結果也沒有說明。
- 建議修正：中位數為 0 時改用絕對門檻或平均值當基期，並在 formula／docstring 寫明。
- 驗證者意見：abnormal_diagnostics.py:534、:554 在中位數為 0 時整段跳過；docstring 公式 ratio≥3 在中位數為 0 時本應成立，而 :20-23 也沒有寫這個例外。我唯讀掃描 data/taiwan/margin 的 136 個交易日：前 20 日中位數為 0 且 |short_change|≥10 萬股的有 185 筆、92 檔，例如 3717.TWSE 在 2026-09-21 為 2,448,000 股；融資 28 筆。觸發條件在真實資料中出現。

### B043（P2）目標日尚無日K時，AI 研究與多股比較把「異常診斷未評估」送成「沒有異常訊號」

- 位置：`backend/app/taiwan/ai_research.py:405`
- 區域：台股資料與分析；類別：correctness；驗證：確認；來源：B3-taiwan-analysis
- 既有文件：docs/audit-2026-09-24/sections/G01-gap.md:31、:38（G01-gap#1 修法建議「ai_research 在 diag_item 為 None 時加入 missing_items」）；recheck-2026-10-06.md:231 判為已修正，但只修了 data_quality，AI 子項仍在。G03-gap#2（recheck:250）改用 price_date 之後才產生這個日期錯位
- 觸發條件：resolve_target_latest_trading_date 從 16:00 起回傳當日，但當日日K要等 16:30 排程或手動更新後才寫入。這段期間（或資料延遲時）觸發個股 AI 研究或多股比較就會發生。
- 影響：研究脈絡以前一交易日（price_date）的價格呈現。診斷卻用 as_of_date（當日），所以沒有任何 item，diag_item 為 None，payload 的 abnormal_signals=[]，missing_items 也沒有標記。AI 會把前一日實際觸發的訊號說成沒有；比較頁每一檔的 diagnostic_item 也都是 None。
- 建議修正：診斷改用 price_context.trade_date。diag_item 為 None，或診斷 data_quality 不是 complete 時，在 missing_items 加入 abnormal_diagnostics。
- 驗證者意見：daily_update.py:46 的截止時間是 16:00，排程在 daily_pipeline.py:250 的 16:30。這段期間 research_context.py:397 讓價格改用前一交易日，:840 的 as_of_date 卻是當日。ai_research.py:405 以當日做診斷：目標日沒有日K時，abnormal_diagnostics.py:383 對每檔都 continue，items 為空。:307-308 給出 abnormal_signals=[]，missing_items 只來自 :123，沒有診斷相關項目，服務層也沒有閘門。comparison.py:236 同樣如此。

### B044（P2）歷史資料包 bootstrap 任務匯入並補齊到最新日後，從不發佈 enriched，也不刷新記憶體快取

- 位置：`backend/app/taiwan/bootstrap.py:536`
- 區域：台股資料與分析；類別：cache-consistency；驗證：確認；來源：B1-taiwan-ingest
- 觸發條件：新安裝或資料不足的使用者在設定頁按「下載歷史資料包」，_run_job 匯入約 650 個交易日的分區，並用 refresh_dates 補到最新交易日後回報 success。
- 影響：kline_daily_enriched 沒有任何新分區，repo 的 enriched、歷史與策略快取也沒有重建。選股讀不到指標（read_enriched_partition 回 None），策略、K 線指標、監控訊號都是空的。要等到下一個平日 16:30 排程，或使用者自己再按「更新到最新」才會恢復；週末安裝要等到週一盤後。前端只讓 history／data status 查詢失效（TaiwanHistoryDataCard.tsx:87-92），畫面顯示「匯入成功」，指標卻是空的。
- 建議修正：同步完成後呼叫 self._publish(False)（或 publish_enriched(only_if_behind=True)），並在 API 或任務完成時呼叫 refresh_enriched_cache／refresh_strategy_cache；或者讓前端在任務成功後接著觸發 update-latest。
- 驗證者意見：bootstrap.py:518-552 的 _run_job 只呼叫 refresh_dates 就標為 success，沒有呼叫 self._publish 或 publish_enriched。git 的 612ea7a 只替 update_to_latest 加上發佈；main.py 啟動時只預熱快取，不會發佈。新安裝後 enriched 是空的，要等平日排程的 run_update（daily_update.py:856 一定會發佈）或手動按「更新到最新」才補上。前端只讓 history 與 data status 失效（TaiwanHistoryDataCard.tsx:87-90）。

### B045（P2）個股詳情在即時抓到融資資料時，券資比恆為空

- 位置：`backend/app/taiwan/detail_service.py:588`
- 區域：台股資料與分析；類別：correctness；驗證：確認；來源：B3-taiwan-analysis、K1a-audit-recheck
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md:155 A07-chips#1（未修正）；recheck-2026-10-06.md A07-chips#1；README P2 第 12 點
- 觸發條件：載入個股頁，且 margin_provider.fetch_live_day 抓取成功（交易日盤後、有融資資料）。
- 影響：券資比顯示「--」，factors.short_margin_ratio 也是空值；只有即時抓取失敗、改走 store fallback 時才有值。
- 建議修正：即時路徑以 short_balance / margin_balance × 100 計算券資比，或沿用 margin_store 的計算。
- 驗證者意見：enrichment/margin.py:130（TWSE）與 :228（TPEx）建構 MarginTrading 時固定 short_margin_ratio=None。models.py:183 註明此值由 factor 層衍生，但 detail_service.py 的即時路徑 :582-589 原樣帶入，沒有補算。只有 store fallback（:627）有值。:658 讓 factors 也拿到 None。前端 TaiwanStockDetail.tsx:772、:822 為 null 時顯示 --。與 A07-chips#1 一致，仍未修正。
- 同一問題的其他回報（K1a-audit-recheck，評 P2，位置 `backend/app/taiwan/detail_service.py:588`）：詳細頁的即時路徑一律把券資比設成 null；驗證者意見：detail_service.py:588 原樣帶入 m.short_margin_ratio。margin.py:130、:228 的 TWSE/TPEx adapter 固定給 None，fetch_live_day(:248-268) 也沒有再算；models.py:183 註明此欄應由因子層計算。:658 沿用 None，TaiwanStockDetail.tsx:772、:822 遇到 null 顯示 '--'。只有走 :627 的 store fallback 才有值(margin_store.py:129 會補算)。觸發路徑成立。

### B046（P2）詳細頁的「所屬大盤基準」永遠是 unavailable：TaiwanIndexProvider 只有解析函式，呼叫端固定傳空陣列

- 位置：`backend/app/taiwan/detail_service.py:680`
- 區域：台股資料與分析；類別：functional；驗證：確認；來源：B2-taiwan-chips-providers、B3-taiwan-analysis、K1a-audit-recheck、K1c-audit-sections、X1a-contract-fe-to-be
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md:249 G03-gap#1（P2，未修正）；docs/audit-2026-09-24/README.md:620；docs/audit-2026-09-24/recheck-2026-10-06.md:249 G03-gap#1（未修正）；docs/audit-2026-09-24/sections/G03-gap.md G03-gap#1（recheck-2026-10-06.md:249 標未修正）；recheck-2026-10-06.md G03-gap#1、G03-gap#8（RC15）；README P2 第 9 點；sections/G03-gap.md G03-gap#1（recheck 判未修正）
- 觸發條件：開啟任何個股詳細頁。
- 影響：大盤收盤與漲跌幅一律顯示「--」；本地 index_daily store 有資料也沒有用上。
- 建議修正：改讀 index_daily store（t00.TWSE／o00.TPEX），用前一筆收盤計算漲跌，並標示資料日期；或刪除這張卡。
- 驗證者意見：detail_service.py:680、:682 固定傳空陣列，indices 永遠是空的，會走 :701-707 的 unavailable。index.py 只有 parse_* 方法，沒有抓取或讀 store 的方法；全 repo 也只有這裡呼叫。前端 TaiwanStockDetail.tsx:493-501 因此顯示「--」。recheck-2026-10-06.md:249 G03-gap#1 也標為未修正。
- 同一問題的其他回報（B3-taiwan-analysis，評 P2，位置 `backend/app/taiwan/detail_service.py:680`）：個股詳情「所屬大盤基準」恆為 unavailable；驗證者意見：detail_service.py:680、:682 對 parse_tpex_rows／parse_taiex_rows 固定傳入空陣列。enrichment/index.py:71、:131 的 for 迴圈不會執行，回傳 []，所以 :683 不成立，落到 :701-708 回 unavailable。同檔 :41 匯入 get_index_daily_service，:296 也在讀本機 index_daily（540 個分區），資料其實存在。與 G03-gap#1 一致，仍未修正。
- 同一問題的其他回報（X1a-contract-fe-to-be，評 P2，位置 `backend/app/taiwan/detail_service.py:680`）：個股詳細頁「所屬大盤基準」卡永遠顯示 --：後端 market_context 的 close／change／change_pct 一律回 null；驗證者意見：親自讀到 detail_service.py:678-682 固定傳空陣列給 parse_tpex_rows([])／parse_taiex_rows([])，enrichment/index.py:62-71 只迭代傳入的 rows，所以 indices 一定是空的，必定走 701-708 的 fallback，close/change/change_pct=None。TaiwanStockDetail.tsx:497-502 直接渲染這些欄位，結果永遠顯示 --。後端 market_context 只在 :186 這一處產生，沒有其他覆寫。recheck-2026-10-06.md:249 G03-gap#1 也列為未修正（行號已漂移）。這是明確的功能錯誤，維持 P2。
- 同一問題的其他回報（K1a-audit-recheck，評 P2，位置 `backend/app/taiwan/detail_service.py:680`）：詳細頁的「所屬大盤基準」卡永遠顯示「--」；驗證者意見：detail_service.py:680、:682 呼叫 parse_tpex_rows([])／parse_taiex_rows([])。index.py:62 起的 for 迴圈遇到空陣列直接回傳 []，所以一定落到 :701 的 unavailable。:186 是唯一呼叫點，其他地方也沒有覆寫。前端 TaiwanStockDetail.tsx:498 收到 null 就顯示 '--'。index.py:86 的 prev_close=open_idx 經確認也存在，但目前沒有呼叫端會帶入非空資料，所以只是潛在問題。行號應為 680(呼叫處)。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `backend/app/taiwan/detail_service.py:680`）：個股詳細頁「所屬大盤基準」卡永遠顯示 --（以空陣列解析指數）；驗證者意見：detail_service.py:680、:682 對 parse_tpex_rows 與 parse_taiex_rows 傳入空陣列。enrichment/index.py:62-120 只迭代傳入的 rows，空輸入一定回傳 []，所以 :683 的 `if indices:` 永遠不成立，最後走到 :700-707 回傳 close、change_pct 皆為 None。前端 TaiwanStockDetail.tsx:498-503 因此恆顯示「--」。detail_service.py 不在進行中的檔案清單內。

### B047（P2）增量 enriched 發佈直接 os.replace，沒有 Windows 重試，也繞過 EnrichedPublication（不換 generation、不互斥）

- 位置：`backend/app/taiwan/enriched_refresh.py:99`
- 區域：台股資料與分析；類別：concurrency-file-lock；驗證：確認；來源：B1-taiwan-ingest
- 既有文件：docs/code-review-2026-09-22.md:818-819（已知限制：「部分重發佈與盤中寫盤兩條直接取代檔案的路徑，遇到同時讀取仍可能偶發失敗，下一班會補回」）；generation 被繞過這一點沒有記錄
- 觸發條件：每天 16:30／21:40 的盤後發佈或「更新到最新」走非全量路徑（refresh() 第 190 行 _publish_partitions(enriched) 不帶 publication）時，同一時間有讀者持有目標分區：回測或矩陣預熱的 pyarrow dataset 掃描（backend/app/backtest/matrix.py:760，root 為 engine.py:906 的 enriched 目錄），或 DuckDB 對 kline_enriched view 的查詢（repository.py:1083、2275；services/screener.py:655）。
- 影響：Path(tmp_path).replace(target) 以 PermissionError WinError 5 失敗，發佈在迴圈中途中斷。窗口內前面幾天已換新、後面幾天仍是舊值，當天分區可能缺漏。publish_enriched 只在 note 記一筆「Enriched publish failed」（daily_update.py:390-393），daily 狀態不變。排程接著照常跑 refresh_enriched_cache 與 refresh_strategy_cache，選股基準日的指標可能缺漏或過時，要等下一班才補回。另外這條路徑不換 generation，以 generation 為鍵的快取（repository.py:1955-1990 的 _published_daily_cache；回測 assert_data_generation）偵測不到這次改寫；以 CLI 在另一個行程發佈時，伺服器端的快取會一直留著舊列。這條路徑也不和同時進行的全量 publication 互斥。
- 建議修正：增量路徑改走 EnrichedPublication.write_parquet，至少改用 _replace_with_retry，並在提交後換新 generation；或者在發佈期間取得同一把 generation 鎖。
- 驗證者意見：enriched_refresh.py:91-103 沒有重試；refresh():190 的非全量路徑不經 EnrichedPublication，全程也沒有呼叫 bump_enriched_generation。Windows 取代失敗已列為已知限制（code-review-2026-09-22.md:818-819）。generation 未換造成的影響更具體：matrix.py:783-792、1758 帶 source_generation 時略過分區指紋，21:40 重抓暫定日之後，回測仍會重用舊的矩陣磁碟快取（預設啟用）。同一行程內的 _published_daily_cache 會由 refresh_cache 清除（repository.py:666）。

### B048（P2）台股部分窗口發佈繞過 EnrichedPublication，generation 不變，以 generation 為鍵的快取會讀到舊資料

- 位置：`backend/app/taiwan/enriched_refresh.py:237`
- 區域：台股資料與分析；類別：cache-consistency；驗證：確認；來源：B8b-indicators-builtin
- 觸發條件：每日排程或「更新到最新日」走 refresh_recent()／refresh(start, end) 的非全量路徑（daily_update.py:389 呼叫），重寫最近 5 個或 stale_since 起的 enriched 分區，例如某天的 TPEx 快照先失敗、之後補抓並重新發佈同一天
- 影響：分區內容已變，.matrix_generation_stock.json 的 generation 卻沒變。(1) 回測、優化、選股用的矩陣磁碟快取：有 source_generation 時完全不比對分區指紋（matrix.py:783-791 把 requested_partitions 設成 {}，:1758-1760 只比 generation），覆蓋該日期區間的舊快取會被直接沿用，訊號與成交建立在修補前的價量上；(2) repository._published_daily_cache 以 (generation, symbol, start, end) 為鍵（repository.py:1963），refresh_cache() 不會清這個快取（只有 clear_cache 的 :666 會清），所以 K 線與關鍵價位會回傳修補前的列。這違反 CONTRIBUTING 6.1 和 repository.py:1957-1961 docstring 自己寫的契約：每次寫分區都必須經過 EnrichedPublication
- 建議修正：部分窗口改用 EnrichedPublication（begin → write_parquet → commit），或寫完後呼叫 bump_enriched_generation；同時讓寫入受 _exclusive_generation_lock 保護，換檔時套用 Windows 重試。另補一個測試：部分發佈後 generation 必須改變
- 驗證者意見：親讀確認：enriched_refresh.py:190 非全量時呼叫 _publish_partitions(enriched) 沒有傳 publication，:237 改走 _write_partition（:91-103），不寫 marker、不換 generation。全 repo 的 bump_enriched_generation 只有 repository.py:2251 包裝，正式碼沒有呼叫者。matrix.py:783-791 在有 source_generation 時不比對分區指紋，engine.py:929-956 會傳入 generation；repository.py:1963 的快取鍵含 generation，refresh_cache 不會清它。audit README:206 也實際記錄到部分窗口發佈後 .matrix_generation_stock.json 沒有改，可作旁證。

### B049（P2）詳細頁每次開啟都同步下載全市場法人與融資表，沒有快取，最多 10 次請求，每次 3 秒逾時

- 位置：`backend/app/taiwan/enrichment/institutional.py:260`
- 區域：台股資料與分析；類別：performance；驗證：確認；來源：B2-taiwan-chips-providers
- 觸發條件：開啟個股詳細頁。當日資料未公布時，每個 dataset 至少請求 2 次（今天與前一日）；週五遇到交易所沒有回應時，每個 dataset 往回試 5 個工作日。
- 影響：每次都下載上市 T86（約 1,300 列）、上櫃 dailyTrade sect=AL（約 5,000～6,000 列，含權證）與兩份融資全表，重開同一頁也重抓。交易所慢或封鎖時，法人與融資依序各等最多 5×3 秒，頁面延遲可達約 30 秒；短時間連開幾頁會觸發 TWSE 限流，連帶影響排程抓取。
- 建議修正：盤後優先讀 store（refresh 已經抓過全市場）；即時抓取加上以 (exchange, date) 為鍵的 TTL 快取，並在 store 已有該日時跳過網路請求。
- 驗證者意見：institutional.py:260-279、margin.py:248-267 每次都下載全表，沒有快取；detail_service.py:412-427、:556-571 依序重試，timeout 3 秒。另外前端 TaiwanStockDetail.tsx:156 設了 refetchInterval 10 秒，所以頁面開著時每 10 秒就重抓：盤中當日未公布時約 4 次全表請求，限流風險比原宣稱高，因此升為 P2。修正一點：每次只下載該股所屬交易所的表，不是兩所都下載。

### B050（P2）詳細頁的融資即時路徑永遠不算券資比，只要即時抓取成功，「券資比」與「最新券資比因子」就顯示「--」

- 位置：`backend/app/taiwan/enrichment/margin.py:130`
- 區域：台股資料與分析；類別：data-contract；驗證：確認；來源：B2-taiwan-chips-providers、K1c-audit-sections
- 既有文件：docs/audit-2026-09-24/sections/A07-chips.md A07-chips#1（recheck-2026-10-06.md:155 標未修正）；sections/A07-chips.md A07-chips#1（recheck 判未修正）
- 觸發條件：開啟任何上市或上櫃個股詳細頁，而且 MI_MARGN 或 TPEx margin/balance 的即時抓取成功（這是常態）。
- 影響：「融資融券」卡的券資比與「核心籌碼滾動因子」的最新券資比都是空值，只有即時抓取失敗、改走 store 備援時才有值。同一頁的歷史彈窗讀 store，看得到數值。
- 建議修正：把 margin_store 的券資比算式抽成共用 helper（融資餘額大於 0 才算，否則為 None，四捨五入到 2 位），adapter 或 _aggregate_margin 即時路徑呼叫它；補一個即時路徑的測試。
- 驗證者意見：margin.py:130、:228 固定 short_margin_ratio=None；detail_service.py:588 原樣轉傳，:658 因子沿用；只有 margin_store.py:130-136 寫入時才算。前端 TaiwanStockDetail.tsx:772、:822 遇到 null 就顯示「--」。即時抓取成功是常態，所以券資比通常是空值，只有走 store 備援時才有值。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `backend/app/taiwan/enrichment/margin.py:130`）：個股詳細頁即時抓取成功時券資比與「最新券資比因子」恆為「--」；驗證者意見：margin.py:130（TWSE）與 :228（TPEx）的 adapter 固定寫 short_margin_ratio=None。models.py:183 註明這個值應由 factor 層推導，但 detail_service.py:588 的即時路徑直接轉傳 None，:658 的因子也取同一個值。只有 store 備援（:627，margin_store.py:129-135 會補算）才有值。前端 TaiwanStockDetail.tsx:772、:822 遇到 null 時顯示「--」。即時抓取成功是常態，所以券資比通常看不到。

### B051（P2）法人、融資與市場法人金額補抓時，某個新日期失敗仍繼續寫入後面的日期，缺口日之後不會再被排程重抓

- 位置：`backend/app/taiwan/institutional_margin_refresh.py:252`
- 區域：台股資料與分析；類別：data-integrity；驗證：確認；來源：B2-taiwan-chips-providers
- 觸發條件：後端停機數日後的 catch-up（或排程一次補多日）時，區間中某個交易日的 T86／TPEx dailyTrade 或 MI_MARGN／TPEx margin 請求，在 _fetch_json_with_retry 重試 3 次後仍失敗（逾時、IncompleteRead、限流），而之後的日期成功寫入。
- 影響：下一輪 _refresh_institutional／_refresh_margin 用 resolve_missing_date_range 從 store 最大日期往後算，provisional 只檢查已存分區，所以缺口日永遠缺漏。_institutional_5d 取「最近 5 個已存日期」，窗口實際跨 6 個交易日又漏掉缺口日，詳細頁的 5 日法人累計、research_context 的 5／20 日法人與 5 日融資增減都會靜默算錯；歷史視窗把缺口說成「尚未回補」。設定頁狀態只回報 day_count／earliest／latest，看不出缺口；只有手動回補才補得回來。
- 建議修正：比照 daily_refresh：新日期（大於 latest_stored）失敗時 break，不再寫後面的日期；或在 catch-up 時用交易日曆與日線 store 找出區間內缺少的籌碼分區並重抓，並補一個「中間日失敗後下一輪會補」的回歸測試。
- 驗證者意見：institutional_margin_refresh.py:252-256 新日期失敗只記 failed_dates 後繼續，:369-371 融資相同；market_institutional_refresh.py:104-124 也是記下後繼續。daily_update.py:545、:629 從 store 最大日期往後算，:550 的 provisional 只檢查已存分區，缺口日不會再被抓。daily_refresh.py:230-233 有 break 保護，籌碼三個 refresh 都沒有。detail_service.py:519 取最近 5 個已存日期，所以窗口會跨過缺口。測試裡沒有中間日失敗的案例。

### B052（P2）融資 refresh 只要任一交易所有資料就寫入，並永久視為已完成；設定頁「回補」在 16:00 後就抓當日融資，繞過 21:40 的發布閘門

- 位置：`backend/app/taiwan/institutional_margin_refresh.py:338`
- 區域：台股資料與分析；類別：data-integrity；驗證：可能成立；來源：B2-taiwan-chips-providers
- 既有文件：docs/audit-2026-09-24/sections/A07-chips.md「無法驗證的項目」第 574 行（單一交易所分區永久缺漏，當時列為推測風險）
- 觸發條件：(a) 交易日 16:00～21:40 之間在設定頁按「回補」：enrichment_backfill 用預設 16:00 cutoff 決定 end_date＝今天，對當日也抓融資；這時如果上市或上櫃只有一邊已公布，或某一邊回空表（stat ok 但 data 空）。(b) 任何時候某一邊的回應是空表，另一邊有資料。
- 影響：該日分區只有一個交易所。之後 available 集合讓它永遠被跳過（融資沒有 provisional 機制，法人有 one_sided 保護但融資沒有），另一個交易所當天的融資餘額、增減與券資比永久缺漏。選股器的 read_latest_per_symbol 對那些股票改用前一日的列；市場層級融資彙總少一個市場；詳細頁的 store 備援也退回舊日期。
- 建議修正：融資比照法人：只有上市與上櫃都有列時才寫入，單邊時列入 repair_failed_dates，不寫分區；enrichment_backfill 的融資 end_date 改用 resolve_margin_target。
- 驗證者意見：程式邏輯成立：:338 只要任一邊有列就寫入，:291/:314 之後永遠跳過該日，融資沒有法人 :208-214 那種 one_sided 防護。enrichment_backfill.py:127 用 16:00 cutoff，:251 用同一個 end_date 抓融資，繞過 resolve_margin_target 的 21:40 閘門。但兩所融資大約都在 21:00 公布，A07 文件在 119 個分區中也沒看到單邊分區，觸發頻率無法確認。

### B053（P2）即時輪詢每一輪都新建 TaiwanHybridProvider 與 httpx.Client，本機實測每次約 0.3～0.43 秒 CPU

- 位置：`backend/app/taiwan/providers/hybrid_provider.py:44`
- 區域：台股資料與分析；類別：performance；驗證：確認；來源：B2-taiwan-chips-providers
- 既有文件：docs/audit-2026-09-24/sections/A13-realtime.md A13-realtime#2（:304、:324 提到每輪新建 provider 與 httpx.Client；修正只加了 payload 快取，建構成本仍在）；G02-gap#5（基本面每次 new provider，recheck 標未修正）
- 觸發條件：台股模式開啟即時行情時，quote_service 每一輪（預設 6 秒，pro／expert 檔位 3／1 秒）都呼叫 get_provider("taiwan")，registry 每次執行 provider_cls()。另外，/api/taiwan/data/{symbol} 每次請求都會新建 TaiwanCurrentDataService 與 TaiwanHybridProvider，個股的 4 個 dataset 又各自新建 TaiwanOfficialFundamentals（各帶一個 httpx.Client）。
- 影響：每輪多花 0.3～0.43 秒，而且是在 _fetch_lock 內。6 秒間隔約佔單核 5～7%，1 秒間隔約 30～40%。個股資料頁每次請求光建構 client 就約 1.5～2 秒。這些 client 從不關閉，連線池要等 GC 才釋放。
- 建議修正：讓 get_provider 回傳模組層級的單例，或讓 OfficialTaiwanAdapter／TaiwanOfficialFundamentals 共用同一個模組層級的 httpx.Client；current_data 也改成共用 service。
- 驗證者意見：registry.py:16 每次都執行 provider_cls()，quote_service.py:750 持鎖，經 :792 進到 :901，每輪都呼叫 get_provider；official_provider.py:76 每次新建 httpx.Client。本機實測 httpx.Client 每次 268～579ms，其中 CPU 265～422ms；get_provider 329～694ms，TaiwanOfficialFundamentals 322～405ms，兩次 get_provider 回傳不同實例。current_data.py:273 每次請求也都 new。

### B054（P2）單一非台股或非 canonical 代號會讓整輪台股即時行情歸零

- 位置：`backend/app/taiwan/realtime/service.py:91`
- 區域：即時行情；類別：correctness；驗證：確認；來源：B4-realtime-universe-rules
- 既有文件：未見於既有審查文件
- 觸發條件：自選清單或 scope=symbols 的監控規則裡有一個解析不了的代號，例如舊 A 股的 600519.SH，或沒有後綴的 2330。監控規則 API 的 _validate_taiwan_symbols 明文略過非台股格式、照樣存檔（api/monitor_rules.py:43-59）；自選 API 對代號完全不驗證（api/watchlist.py:81-83）。quote_service 建輪詢池時也沒有過濾（quote_service.py:776-797，而且規則代號排在最前面）。
- 影響：get_quotes 在 :91 逐筆 parse_symbol，遇到第一個無法解析的代號就拋 ValueError，整批放棄。hybrid get_realtime 接住例外，只記一行 WARNING（hybrid_provider.py:275）；08:30–13:30 又不允許官方備援（:276），所以回傳空表。結果所有標的的今日K、分鐘K、監控告警、SSE 更新全部停住，直到移除該代號為止。另外 /api/intraday/quotes 只要帶入同一個代號就回 HTTP 500（api/intraday.py:129）。
- 建議修正：get_quotes 改成逐筆解析，無法解析的跳過並記錄 debug；quote_service 建池時用 is_taiwan_symbol 過濾，非台股代號不送進台股 provider。另補一個「混入非法代號仍回傳其他標的」的回歸測試。
- 驗證者意見：service.py:91 直接呼叫 parse_symbol，沒有 try；symbol.py:149 遇到 .SH 或無後綴的代號會 raise。hybrid_provider.py:274-277 在 08:30–13:30 直接回空表，quote_service.py:795 因此整輪略過。輸入面也打得通：watchlist.add_batch 不驗證代號；api/watchlist.py:297-299 明確把 legacy 代號分流，等於承認自選可能混有非台股代號；輪詢池 :782 卻沒有過濾。TaiwanQuotePanel 直接把自選代號送進 /quotes，會回 500。

### B055（P2）研究脈絡的 5 日／20 日報酬與 20 日高低點按「有收盤價的列」計數，不是按交易日

- 位置：`backend/app/taiwan/research_context.py:438`
- 區域：台股資料與分析；類別：data-definition；驗證：確認；來源：B3-taiwan-analysis
- 既有文件：沒有被記錄。G03-gap#5（recheck:253）只談 high_20d/low_20d 以收盤價計且未標示口徑
- 觸發條件：標的在近 20 個交易日內有無成交或停止交易的日子（日K列 close=null），例如冷門股、處置股、停牌後復牌的個股。
- 影響：return_5d 可能其實是 16 個交易日的報酬，return_20d、high_20d、low_20d 也跨越更長期間，欄位卻仍標 5D/20D。AI 研究（ai_research.py:185-197）與多股比較把它當成同期間的數字並列。產業強弱與 RELATIVE_STRENGTH_OUTLIER 用的是交易日基期（industry_intelligence.py:202-203、abnormal_diagnostics.py:346-349），同一檔在不同頁面的報酬期間不同。違反 CONTRIBUTING 3.3「窗口、前 N 日按實際交易日」。
- 建議修正：以 available_dates 取第 t-5、t-20 個交易日的列，那天沒有成交就回 null；20 日極值限定在最近 20 個交易日的窗口內。
- 驗證者意見：:438 先 drop_nulls 再以 :441 closes[-6]、:445 closes[-21]、:450 closes[-20:] 計算，按有收盤價的列計數，不是按交易日。實讀 8923.TPEX：近期有收盤價的列為 9/15、9/21、9/22、9/29、9/30、10/5、10/8、10/20、10/21，closes[-6] 是 9/29 的 18.45，17.95/18.45-1=-0.0271，與宣稱一致。按交易日算的 5 日基期 10/14 收盤為 null。違反 CONTRIBUTING 3.3。

### B056（P2）台股選股每次請求都讀取全部日K歷史分區，以及全部法人與融資分區

- 位置：`backend/app/taiwan/screener.py:228`
- 區域：台股資料與分析；類別：performance；驗證：確認；來源：B3-taiwan-analysis
- 既有文件：效能面沒有被記錄。語意面（每檔取自己最新一列）見 docs/audit-2026-09-24/recheck-2026-10-06.md:158 A07-chips#4（未修正）
- 觸發條件：任何一次 POST /api/taiwan/screener/run 都會觸發。選股頁每次改條件、翻頁或排序都會送出新請求，查詢鍵是 payload，staleTime 10 秒。
- 影響：單次請求約 5 秒，耗時隨歷史分區數線性增加（目前 678 個日K分區）。read_latest_per_symbol 經 read_all→read_range 逐一 scan+collect 全部日K分區；法人與融資的 read_latest_per_symbol 也讀全部分區。這違反 CONTRIBUTING 6.3「需要歷史窗口時只讀取必要範圍」。
- 建議修正：日K只讀最新分區（再加上需要時的前一分區），或改用會把條件下推的 scan_range 並限定日期。法人與融資同樣限定窗口，並先依 A07#4 決定「缺列」的語意。
- 驗證者意見：screener.py:228 呼叫 read_latest_per_symbol。daily_store.py:242 走 read_all，再由 :265-287 對每個 date=* 分區各自 scan 並 collect。法人與融資在 institutional_store.py:214、margin_store.py:214 也是 read_all。api/taiwan.py:458-464 沒有快取，前端 staleTime 只有 10 秒。實測分區數：日K 678、法人 136、融資 136。純讀檔約 1.7 秒（日K 1.20 秒），只讀最新分區約 0.002 秒，耗時隨歷史線性增加，違反 CONTRIBUTING 6.3。

### B057（P2）test_taiwan_research_context 的 mock read_range 不看日期，產業快照連續多對多 join 膨脹到 1384 萬列，全套 pytest 吃到約 20.8 GB 後以 access violation 崩潰

- 位置：`backend/tests/test_taiwan_research_context.py:248`
- 區域：測試；類別：test；驗證：確認；來源：T2-backend-tests
- 既有文件：docs/audit-2026-09-24/README.md:321（只記錄「約佔 19～22 GB RSS、全量回歸要另開行程」，沒有指出原因）
- 觸發條件：執行 `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider -rfE`（全套同一行程），跑到使用 `_indicator_service` 的 6 個技術脈絡測試（:311、:327、:335、:351、:367、:388）
- 影響：全套後端回歸無法在同一行程跑完：本次第一輪在約 87% 處印出 `Windows fatal exception: access violation`，退出碼 139，沒有 short test summary。行程峰值約 20.8 GB，與使用者正在跑的 uvicorn 搶記憶體。後續驗收只能排除這個檔、另開行程跑。
- 建議修正：`_indicator_service` 的 read_range 改用 side_effect，依 (symbols, start, end) 過濾日期；或者在 helper 裡把 industry_intel_svc 與 market_intel_svc 換成 MagicMock（後兩個測試已經對 market_intel_svc 這樣做）。
- 驗證者意見：親自讀過：:248 的 return_value 不看參數；research_context.py:331-338 把同一個 mock daily_store 傳進產業服務；industry_intelligence.py:241-244 讀了 4 次，:293/:313/:329/:336 依 symbol 連續 left join。我用 join 探針跑 :335 的測試，記到 (1,61)→(61,61)→(3721,61)→(226981,61)，在下一步 1384 萬列之前中止。README.md:321 另外記錄了約 19～22 GB RSS。產品程式不受影響，但全套回歸被卡住，記憶體也會跟服務搶，維持 P2。

### B058（P2）分時圖成交額把 1e9 當成「億」，金額少顯示 10 倍

- 位置：`frontend/src/components/EChartsIntraday.tsx:43`
- 區域：前端；類別：display-unit；驗證：確認；來源：K1a-audit-recheck、F5-components-charts、K1c-audit-sections
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md:272 G05-gap#3（未修正）；docs/audit-2026-09-24/sections/G05-gap.md:416-441；recheck-2026-10-06.md G05-gap#3（RC16）；README「目前待修正項目」P2 第 8 點；sections/G05-gap.md G05-gap#3（recheck 判未修正）
- 觸發條件：在分時圖或多日分時圖把滑鼠停在某一分鐘，該分鐘成交額在 1 億元以上（例如 2330 在 13:30 收盤集合競價那一分鐘，成交額通常有數十億元）
- 影響：成交額在 10 億元以上時，程式除以 1e9 再加「億」，例如 50 億元會顯示成「5.00億」，少了 10 倍；1 億到 10 億元之間會顯示成「50000萬」，不會換成億。多日分時的資訊列也一樣。
- 建議修正：億的門檻改成 1e8，或直接共用 frontend/src/lib/format.ts 的 fmtBigNum。
- 驗證者意見：親讀 EChartsIntraday.tsx:43-47 的 fmtAmt 與 EChartsMultiDayIntraday.tsx:43-47 的 formatAmount，兩者都以 >=1e9 除以 1e9 再標「億」(1億=1e8)，使用處分別在 :618、:459。minute_recorder.py:239 的 amount=price*delta，單位是元，所以 50 億元會顯示成 5.00億，5 億元會顯示成 50000萬。lib/format.ts:23 的 fmtBigNum 用的是正確的 1e8 門檻。宣稱成立。
- 同一問題的其他回報（F5-components-charts，評 P2，位置 `frontend/src/components/EChartsIntraday.tsx:44`）：分時與多日分時的資訊列以 1e9 當「億」，成交額 ≥10 億時少顯示 10 倍，1 億到 10 億之間則退回「萬」；驗證者意見：EChartsIntraday.tsx:44 與 EChartsMultiDayIntraday.tsx:44 都用 1e9 當門檻與除數，後面卻接「億」（1 億是 1e8）；使用處在 :618 與 :459。lib/format.ts:23 的 fmtBigNum 用 1e8。G05-gap.md 已用官方日成交金額交叉驗證 amount 的單位是元，本地也有 ≥1e9 的分鐘棒。recheck:272 G05-gap#3 記為未修正。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `frontend/src/components/EChartsIntraday.tsx:44`）：分時圖與多日分時把 10 億當成「億」，成交額 10 億以上顯示小 10 倍、1～10 億顯示成數萬「萬」；驗證者意見：EChartsIntraday.tsx:43-47 的 fmtAmt 與 EChartsMultiDayIntraday.tsx:43-47 的 formatAmount 都寫成 v>=1e9 才除以 1e9 並加「億」，介於 1e8 與 1e9 之間的值則除以 1e4 加「萬」。分鐘成交額來自 minute_recorder.py:239、256 的 price×delta，單位是元，大型股單分鐘常超過 1 億。20 億會顯示成 2.00億，5 億會顯示成 50000萬。使用處在 :618 與 :459。放大 10 倍的顯示錯誤屬明確功能錯誤，維持 P2。

### B059（P2）常駐的個股預覽對話框把日 K 區間凍結在掛載當下（而且用 UTC 日期），掛載之後的交易日都不會出現在日 K 與資訊條

- 位置：`frontend/src/components/StockPreviewDialog.tsx:66`
- 區域：前端；類別：correctness；驗證：確認；來源：F5-components-charts
- 觸發條件：StockPreviewProvider 在 Layout 內一直掛著 StockPreviewDialog（Layout.tsx:385、StockPreviewProvider.tsx:40-44），Watchlist 與 Dashboard 頁也一直掛著。dateRange 只在第一次掛載時用 useState(getDefaultRange) 算一次，關閉或換股都不重設。觸發情境一：App 或分頁跨日不重新載入（桌面版常見），隔天再開任一檔預覽。觸發情境二：在台北時間 00:00–08:00 開啟 App，此時 getDefaultRange 用 toISOString 取到的是 UTC 日期，也就是前一天。
- 影響：日 K 查詢帶的是凍結的 end_date，後端 get_daily 用 end 過濾（backend/app/api/kline.py:581、:592），所以掛載日之後的已發佈日 K 全部缺席。即時行情關閉時，StockInfoBar 會把凍結那天的收盤價當成「現價」顯示，漲跌幅也是那天的。即時行情開啟時，後端注入今日活蠟燭時不檢查 end（kline.py:785-805），K 線因此出現跳日缺口，前端自算的 VOL5、VOL10 與量比（volMaN、volumeRatioAt）也用錯了前 N 根。
- 建議修正：每次開啟（symbol 由 null 變成有值，或換股）時重算預設區間；或者只保存使用者選的 preset 月數，在 render 時依當下日期換算。getDefaultRange 改用台北時區的日期字串，不要用 toISOString。
- 驗證者意見：StockPreviewDialog.tsx:66 的 useState(getDefaultRange) 只在掛載時算一次，全檔只有使用者操作會呼叫 setDateRange（:221、:235、:241）。對話框常駐在 StockPreviewProvider.tsx:40-44，換股或關閉都不重設。StockDailyKChart.tsx:115 的 end 取自 toISOString（UTC）。後端 kline.py:579、:592 用 end 過濾日 K，但 :603、:627、:634 注入活蠟燭時不看 end。repo 內有 packaging 桌面安裝檔，App 跨日不重載是實際情境。

### B060（P2）同一輪觸發的多筆告警共用同一個 ts：React key 重複，刪除時刪到另一筆

- 位置：`frontend/src/components/monitor/TaiwanAlertsList.tsx:88`
- 區域：前端；類別：correctness；驗證：確認；來源：F6-components-shared
- 觸發條件：一條規則同一輪命中多檔標的，例如作用範圍是自選分組或全市場，這幾筆告警的 ts 會完全相同。使用者在監控中心觸發記錄裡，按下其中「非第一筆」的刪除鍵。
- 影響：(1) 列表 key 是 `${rule_id}_${ts}`，同輪告警 key 重複；每 10 秒重抓一次，React 可能重複或漏渲染列。(2) 刪除用 ts 當識別，後端只刪檔案中第一筆同 ts 的記錄，結果使用者按的那筆還在，另一筆被刪掉，刪錯了使用者的告警歷史。
- 建議修正：後端寫入告警時給每筆唯一 id（例如 uuid，或 ts + 序號），刪除改用 id；前端 key 改用該 id，至少要加上 symbol 與 index。
- 驗證者意見：monitor.py:881 用同一個 now 給同一輪每檔標的的事件打 ts，quote_service.py:1531 以 append_many 依序落盤，事件不帶 alert_id。前端 key 退回 rule_id_ts（:88），同一輪的多筆告警 key 重複。delete_one 只刪第一筆同 ts 的記錄（alert_store.py:148）；list_recent 是穩定排序，畫面順序就是檔案順序，所以按非第一筆的刪除鍵會刪掉第一筆。scope 設成 all 或分組時，同一輪命中多檔是常見情況。

### B061（P2）自然語言選股「套用條件至表單」靜默丟掉 9 個後端白名單欄位

- 位置：`frontend/src/pages/TaiwanScreener.tsx:151`
- 區域：前端；類別：correctness；驗證：確認；來源：B3-taiwan-analysis、F2-pages-main
- 既有文件：沒有被記錄。A12-screener-stats.md:865、:889 只提到 sort_by 不會被套用
- 觸發條件：輸入「投信賣超 500 張以上」「融資減少 1000 張」「量比低於 0.8」「成交額 10 億以下」這類條件。LLM 依提示詞產生 investment_trust_net_max、margin_balance_change_max、vol_ratio_5d_max、amount_max 等 *_max 欄位，使用者再按「套用條件至表單」。
- 影響：翻譯卡片在「已成功解析條件」列出這些條件。套用後 handleApplyTranslation 不處理這些欄位，表單與 payload 也沒有對應欄位，所以選股照常執行，但條件不存在，結果是沒有經過這項過濾的清單。
- 建議修正：前端補齊這些欄位的表單狀態與 payload。或在套用時，對前端無法表達的欄位明確提示「未套用」；也可以讓後端提示詞只列出前端支援的欄位。
- 驗證者意見：TaiwanScreener.tsx:151-177 只處理所列欄位，volume_max、amount_max、momentum_5d_max、vol_ratio_5d_max、investment_trust_net_max、dealer_net_max、margin_balance_change_max、short_balance_max、short_margin_ratio_max 都不處理，:214-250 的 payload 也沒有這些欄位。screener_nl.py:75-86 的白名單列出這些欄位，:60 還示範賣超 max。卡片 :1103-1107 會顯示「已成功解析條件」，套用後條件靜默消失。測試中 grep「套用|handleApply」0 筆。
- 同一問題的其他回報（F2-pages-main，評 P2，位置 `frontend/src/pages/TaiwanScreener.tsx:151`）：AI 自然語言條件「套用至表單」靜默丟棄所有 *_max 上限與 signals 條件；驗證者意見：TaiwanScreener.tsx:151-180 只處理下限與 foreign_net_max，沒有任何 short_margin_ratio_max、volume_max 等上限或 signals 分支，表單也沒有券資比上限輸入框。screener_nl.py:79-87 白名單明列這些 *_max，:219 以 TaiwanScreenerRequest(**sanitized_fields) 建構，後端回傳完整 request。:1047 的 placeholder「券資比低於 5%」會解析成 short_margin_ratio_max=5.0，畫面顯示 ✓，套用後卻被靜默丟掉，篩選結果錯誤。選取中的訊號與量價型態也不會被清除。

### B062（P2）「重置所有篩選」不清除訊號篩選 selectedSignals

- 位置：`frontend/src/pages/TaiwanScreener.tsx:182`
- 區域：前端；類別：state-reset；驗證：確認；來源：F2-pages-main
- 觸發條件：展開進階條件，選取一個訊號（例如「MA金叉」），收合進階條件，再按「重置所有篩選」。
- 影響：handleReset 重置了其他 24 個條件，唯獨漏掉 setSelectedSignals([])。重置後 payload.signals 仍帶著該訊號，結果仍只列出命中訊號的股票。訊號按鈕放在預設收合的進階區塊，重置後畫面看不出還有條件生效，使用者會誤以為看到的是全市場結果。
- 建議修正：在 handleReset 加上 setSelectedSignals([])，並補一個「重置後 payload.signals 為 null」的測試。
- 驗證者意見：handleReset（:182-211）只有 setSelectedVolumePatterns([])（:207），沒有 setSelectedSignals([])。selectedSignals 在 :244 進入 payload.signals，訊號按鈕位於 :1293 showAdvanced 區塊內（:1340-1374），只有該區塊內的「清除」（:1345）會清空。所以收合進階條件後按「重置所有篩選」，訊號條件仍然生效，畫面卻看不出來。

### B063（P2）異常診斷標籤：融券大減仍顯示「融券激增」，部分標籤與訊號庫各自寫死

- 位置：`frontend/src/pages/TaiwanScreener.tsx:996`
- 區域：前端；類別：labeling；驗證：確認；來源：F2-pages-main、K1a-audit-recheck、K1c-audit-sections
- 既有文件：docs/audit-2026-09-24/recheck-2026-10-06.md G01-gap#2（:232）、G01-gap#6（:236）；README.md:249 第 10 點；recheck-2026-10-06.md G01-gap#2（RC17）；README P2 第 10 點；sections/G01-gap.md G01-gap#2（recheck 判未修正）；sections/G01-gap.md G01-gap#6（recheck 判未修正）
- 觸發條件：異常診斷面板出現 SHORT_SURGE、subtype=DECREASE（融券餘額單日大減）的股票。
- 影響：標籤固定寫「融券激增」，方向和實際相反；PRICE_FLOW_DIVERGENCE、RELATIVE_STRENGTH_OUTLIER 的 subtype（價漲外資賣／價跌外資買、LEADER／LAGGARD）也沒有區分。標籤名稱寫死在 JSX，和 lib/signals.ts 的 ABNORMAL_SIGNAL_DEFINITIONS 各自一套。
- 建議修正：依 subtype 顯示「融券激增／融券大減」，並改由 ABNORMAL_SIGNAL_DEFINITIONS 產生標籤名稱。
- 驗證者意見：TaiwanScreener.tsx:996 的 SHORT_SURGE 固定顯示「融券激增」，不看 subtype；:995 的 MARGIN_SURGE 有看。abnormal_diagnostics.py 在 s_chg<0、abs≥100k、倍數≥3 時會產生 subtype=DECREASE，觸發條件可達。背離訊號（PRICE_UP_FOREIGN_SELL／PRICE_DOWN_FOREIGN_BUY）與 LEADER/LAGGARD 也沒有區分。已記錄於 recheck G01-gap#2、#6（未修正，舊行號 971／966-974）。
- 同一問題的其他回報（K1a-audit-recheck，評 P2，位置 `frontend/src/pages/TaiwanScreener.tsx:996`）：異常清單的融券大減也顯示成「融券激增」；驗證者意見：TaiwanScreener.tsx:996 寫成 sig.type==='SHORT_SURGE' && '融券激增'，沒有看 subtype；:995 的 MARGIN_SURGE 則依 subtype 區分激增／大減。abnormal_diagnostics.py:559 在 s_chg<0 時給 DECREASE，而 :554 用的是 abs(s_chg)，所以大減也會觸發這個訊號。前端只在 tooltip 露出負的觀測值，標籤方向相反。宣稱成立。
- 同一問題的其他回報（K1c-audit-sections，評 P2，位置 `frontend/src/pages/TaiwanScreener.tsx:996`）：選股頁把 SHORT_SURGE 的「融券大減」標成「融券激增」；驗證者意見：TaiwanScreener.tsx:996 寫 `sig.type === 'SHORT_SURGE' && '融券激增'`，沒有讀 subtype；:995 的 MARGIN_SURGE 有依 subtype 分成兩種文字。後端 abnormal_diagnostics.py:553-558 以 abs(s_chg) 判定是否觸發，再依正負號給 INCREASE 或 DECREASE。選股頁表格沒有融券變化欄，所以融券大減會被標成相反方向的「融券激增」。
- 同一問題的其他回報（K1c-audit-sections，評 P3，位置 `frontend/src/pages/TaiwanScreener.tsx:998`）：異常訊號前端標籤與定義名稱不一致，「價量背離」誤用；驗證者意見：TaiwanScreener.tsx:991、998、999 的標籤是寫死的「成交額倍增／價量背離／產業強弱極端」，篩選器 :846-847 卻用 signals.ts:263、318、328 的 name（成交額放大／價量與法人背離／產業相對強弱異常）。後端 abnormal_diagnostics.py:592-628 判的是價格與外資反向，跟成交量無關，所以「價量背離」是誤標。

### B064（P2）個股頁每 10 秒輪詢加上 SSE 失效，每次都讓後端即時下載全市場 T86 與融資融券，而且沒有快取

- 位置：`frontend/src/pages/TaiwanStockDetail.tsx:156`
- 區域：前端；類別：performance；驗證：確認；來源：F3-pages-detail
- 觸發條件：交易日 09:00–21:00 開著任一個個股詳情頁，或開著內嵌同一元件的預覽對話框（StockPreviewDialog.tsx:445）。當日法人與融資資料尚未公布時最嚴重。
- 影響：每次 detail 請求都會呼叫 _aggregate_institutional 與 _aggregate_margin。當日資料未公布時官方回空，程式再往前抓一個平日，所以每個資料集打 2 次官方 API。fetch_live_day 每次都 urlopen 全市場 JSON，沒有任何快取。10 秒輪詢約等於每分鐘 24 次官方請求；開啟即時行情時，SSE quotes_updated 還會讓 'taiwan-stock-detail' 失效，staleTime 只有 5 秒，所以每輪報價再多打一次。這些資料一天只更新一次。推測：交易所對短時間的連續請求會節流或封鎖 IP（detail_service.py:466-467 的註解也提到），連帶讓每日管線的官方抓取失敗。
- 建議修正：後端對 live 籌碼抓取依 (exchange, trade_date) 快取到下次發佈時間，或改讀本機 store；前端只對報價段輪詢，或把 detail 的輪詢限制在交易時段、移出 SSE 前綴。
- 驗證者意見：:156 的 refetchInterval 10_000 不分時段，只要分頁可見就會輪詢。queryKeys.ts:129 含 'taiwan-stock-detail'，useQuoteStream.ts:129-160 收到 quotes_updated 時會讓它失效。detail_service.py:403-425 與 :549-571 從今天往回逐個平日呼叫 fetch_live_day（timeout 3 秒），institutional.py:260-279、margin.py:248-260 直接 urlopen 全市場 JSON；api/taiwan.py 與服務層都查無快取。當日未公布時每次刷新打 4 次官方 API。:466 的註解自己承認交易所會節流；連帶影響每日管線屬推測。

### B065（P2）個股詳情頁切換代號後 AI 研究報告沿用前一檔，進行中的請求回來也會寫進新代號頁

- 位置：`frontend/src/pages/TaiwanStockDetail.tsx:175`
- 區域：前端；類別：state-leak；驗證：確認；來源：F3-pages-detail
- 觸發條件：在 /stocks/2330.TWSE 按「生成 AI 研究報告」，報告完成後（或請求還在進行時）用頁首搜尋點選 2317.TWSE（:351-355 navigate 到同一路由）。
- 影響：同一個元件實例被重用，aiReport、aiError、isAiLoading 都沒有重置。2317 頁會顯示 2330 的研究摘要、法人／融資解讀與風險特徵；進行中的 2330 請求回來後，setAiReport 也會把結果寫進 2317 頁。報告物件帶有 symbol、name、evidence_as_of（api.ts:1642-1649），但頁面沒有顯示，使用者看不出報告屬於別檔。違反 CONTRIBUTING 第 7 節「切換股票後不得把上一上下文的結果當作當前結果」。
- 建議修正：依 symbol 重置 AI 狀態（useEffect [symbol]），並比照比較頁用 request key 丟棄過期回應；或在路由／外層以 key={symbol} 強制重新掛載。
- 驗證者意見：TaiwanStockDetail.tsx:175-194 的 aiReport／isAiLoading／aiError 只在 handleGenerateAiReport 內設定，檔內完全沒有 useEffect，也沒有 request key。router.tsx:99 沒有 key，Layout.tsx:755 的 <Outlet /> 也沒有。React Router v6 在 params 改變時會重用同一個元件，:351-355 的 navigate 之後，:1041-1153 仍會渲染上一檔的報告；進行中的請求回來也會寫進新頁。比較頁 TaiwanStockCompare.tsx:100-133 已有防護，詳情頁沒有。屬明確功能錯誤，維持 P2。

### B066（P2）自選頁數值篩選以原始值比較，和畫面上的百分比單位不一致

- 位置：`frontend/src/pages/Watchlist.tsx:1174`
- 區域：前端；類別：units；驗證：確認；來源：F2-pages-main
- 觸發條件：在自選頁開啟篩選，於「漲跌幅」（或振幅、年化波動、5D～60D 動量）的 min 輸入 3，想篩出漲幅 3% 以上的股票。
- 影響：filteredRows 用 getSortValue 的原始小數值（0.03）直接和 Number('3') 比較，所有列都會被隱藏。同一個篩選面板裡，換手率卻是百分數（3 就是 3%），成交額是元（畫面顯示億／萬），輸入框都沒有標單位。使用者依畫面輸入的門檻得到錯誤結果，不違反 CONTRIBUTING 3.1 就無從判斷該輸入 0.03 還是 3。
- 建議修正：依欄位單位定義輸入換算（百分比欄 ÷100、金額欄用萬／億或百萬），並在輸入框旁標示單位；補一個以漲跌幅 3% 篩選的測試。
- 驗證者意見：Watchlist.tsx:1174-1175 直接拿 getSortValue 的原始值和 Number(f.min/max) 比較，沒有單位換算。stock-table.ts:66 的 pct 取 change_pct（小數），primitives.tsx:90 用 fmtPct（×100）顯示，:94 振幅 ×100，:97 換手率則是百分數。輸入框（:1537-1555）只有 min/max placeholder，沒有單位。照畫面在漲跌幅輸入 3 會篩掉所有列，同一面板內各欄單位也不一致。

### B067（P2）AI 設定的「測試」會先把未儲存的表單（含新 API Key）寫入後端，且不更新設定快取

- 位置：`frontend/src/pages/settings/AI.tsx:215`
- 區域：前端；類別：state-consistency；驗證：確認；來源：F4-pages-settings
- 觸發條件：已設定一把可用的 Key K1。使用者在 API Key 欄輸入新 Key K2（或只改模型、API 位址），沒有按「儲存設定」，直接按旁邊或上方的「測試」。
- 影響：handleTest 在 canSave 為真時會呼叫 api.saveAiSettings(payload())，把 K2 和其他欄位寫進 secrets.json。測試失敗時 K1 已被覆蓋，所有 AI 功能都改用 K2，因此全部失效。handleTest 沒有 setQueryData，也沒有 invalidate QK.settings，所以「連線狀態」卡仍顯示舊的 ai_model 和 K1 遮罩，Key 欄 placeholder 仍是「K1 遮罩 · 留空不修改」，使用者會以為舊設定還在。
- 建議修正：後端提供不落盤的測試端點，用候選設定試連。或者前端明示「測試會先儲存」，並在測試成功或失敗後比照 save.onSuccess 更新、失效 QK.settings。
- 驗證者意見：AI.tsx:65 的 canSave 只要求 baseUrl 與 model 非空，所以 215 行在測試前一定會先呼叫 saveAiSettings(payload())。後端 settings.py:266-269、294-295 會覆寫 ai_api_key 並寫入 secrets.json。handleTest（211-223）沒有任何 qc 呼叫，main.tsx 也設了 refetchOnWindowFocus:false，連線狀態卡會一直顯示舊的 model 與遮罩。有一處小誤差：測試後 apiKey 欄仍保留 K2，要清空才會看到舊遮罩的 placeholder。

### B068（P2）資料來源頁的「日K」「除權因子」路由切換對台股資料路徑沒有作用

- 位置：`frontend/src/pages/settings/DataSources.tsx:471`
- 區域：前端；類別：data-contract；驗證：確認；來源：F4-pages-settings
- 觸發條件：在資料來源頁把「日K」或「除權因子」能力卡的標籤點成自訂來源或插件，或在自訂來源上按「使用」或「切換為目前資料來源」。
- 影響：前端 toast「「日K」已切換為 X」，來源卡標示「服務中」，說明文字寫「點誰,該數據集就立刻由誰提供」。但台股日K由台股自己的日K更新流程寫入，復權因子來自 corporate_actions，兩者都不讀 daily_data_provider 或 adj_factor_provider。選股、回測、K 線實際仍用 TWSE/TPEx 資料，使用者以為已改用自有資料源。這違反 CONTRIBUTING §4「provider 缺少能力時應明確提示」。
- 建議修正：台股模式下把日K與除權因子卡標成「固定：台灣官方資料源」，或拿掉切換。若要支援，必須在台股日K寫入路徑接上 provider 路由，並補測試。
- 驗證者意見：在 backend/app Grep daily_data_provider|adj_factor_provider，只命中 kline_sync、extend_history、settings、preferences 四個檔案。run_extend_history 沒有呼叫者，backend/app/taiwan 也沒有讀取 preferences。實際讀取只剩 kline.py:607-620：enriched 為空且本地台股資料也沒有時才回退。前端 471-483 卻 toast「已切換」，plugin-development.md 也宣稱路由可用，所以是明確的功能錯誤。目前沒有任何插件，只有自訂 YAML 能觸發。

### B069（P2）桌面版未打包 app/strategy/prompts/*.md，AI 生成策略時拿不到指南

- 位置：`packaging/tickflow.spec:117`
- 區域：建置與部署；類別：build；驗證：確認；來源：I1-infra-config-deps
- 觸發條件：在桌面安裝版使用「AI 生成策略」或兩步定制流程（prompt_builder.build_step2）
- 影響：spec 的 datas 只收前端 dist、VERSION 與內建策略目錄。prompts 是非 .py 資料檔，PyInstaller 不會自動收集。凍結後 GUIDE_PATH 指向 _internal/app/strategy/prompts/strategy-guide-compact.md，該檔不存在，程式只記一筆 warning 並改用空字串；prompt_builder._load_doc 也會靜默回傳 ""。結果 LLM 收不到欄位名、匯入白名單與 META 格式等規範，生成的策略容易驗證失敗或欄位錯誤，使用者看不到原因。
- 建議修正：在 spec 加入 `datas += [(str(ROOT/'backend'/'app'/'strategy'/'prompts'), 'app/strategy/prompts')]`；文件缺失時應提高到 error 或在 API 回報錯誤。
- 驗證者意見：spec:111-117 的 datas 只收 static、VERSION、builtin，hookspath 是空的，也沒有對 app 呼叫 collect_data_files；prompts 目錄下 4 個 md 都不會進產物。ai_generator.py:140-144 找不到檔案時只記 warning 並回傳 ""，stream 送出的 system prompt 就只剩「--- 策略开发指南 ---」後面接空白；prompt_builder.py:18 也會靜默回傳 ""。release.yml 沒有額外複製步驟。
- 同一問題的其他回報（I1-infra-config-deps，評 P2，位置 `packaging/tickflow.spec:121`）：桌面版打包排除 numba/llvmlite，所有 @njit 核心退回純 Python 迴圈；驗證者意見：spec:120-122 的 excludes 確實含 numba、llvmlite；matrix.py:52-64 與 pipeline.py:45-55 在 ImportError 時把 njit 換成直通、prange 換成 range。我把 _valid_shift_kernel 抄到 暫存目錄 實測 1000×1000：純 Python 1.53s，JIT 0.0097s，約慢 157 倍，結果相同。fd79b63 的註解「主包不含 vectorbt 回測鏈」顯示這是 vectorbt 時期遺留的設定。宣稱說當時 numba 是可選依賴，這點有誤，fd79b63 時它已是核心依賴。

## P3

P3 依區域分組；每列只列重點，觸發條件與建議修正的全文在驗證紀錄中保留，修正前請以位置重讀程式碼確認。

### 前端（77 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B255 | `frontend/src/components/EChartsCandlestick.tsx:882` | EChartsCandlestick 的 volumeCompare 預設值是解構時的物件字面量，父層每次 re-render 都觸發一次全量 setOption(notMerge)，並重派 dataZoom | 把預設值提升為模組常數，例如 `const DEFAULT_VOLUME_COMPARE = { enabled: true, days: 1 }`；activeIndicators 的預設 [] 也一併提升。 | 確認 |  |
| B256 | `frontend/src/components/EChartsCandlestick.tsx:1099` | 日 K 的 dataZoom 回呼抓住掛載當下的 updateCompactPresentation，縮放跨過 60 根門檻時會用舊的 markers、data 與 showMarkers 覆寫標記 | 把 updateCompactPresentation 存進 ref，每次 render 更新 ref.current，回呼改從 ref 呼叫；或者讓它讀 markers、showMarkers、data 的 ref。markPoint 的組裝可與 buildOption 共用同一個函式，避免兩份邏輯不同步。 | 確認 |  |
| B257 | `frontend/src/components/Layout.tsx:7` | Layout 靜態引入預覽視窗，把個股詳細頁與 ECharts 拉進首屏 | StockPreviewProvider 內以 React.lazy 載入 StockPreviewDialog，或 Dialog 內 lazy 載入 TaiwanStockDetail。 | 確認 | docs/code-review-2026-09-22.md 三、次要問題（Layout.tsx 靜態 import StockPreviewProvider） |
| B258 | `frontend/src/components/Layout.tsx:177` | 側欄分組漲跌的查詢 key 與自選頁不同，同一頁會打兩次 /api/watchlist/enriched，每次 SSE 也加倍重抓 | Layout 改用 QK.watchlistEnriched('')，或在 QK.watchlistEnriched 內把空字串統一轉成 undefined。 | 確認 |  |
| B259 | `frontend/src/components/Layout.tsx:191` | Layout 常駐輪詢 pipeline jobs，只為了一個永遠不會出現的 /data 選單指示 | 移除這個 query、dataSyncJustDone 狀態與 /data 分支；若要提示資料同步，改掛到實際存在的設定頁資料卡。 | 確認 |  |
| B260 | `frontend/src/components/Layout.tsx:268` | 側邊欄即時行情把預設資料源 'taiwan' 當成自訂源，顯示原始英文 ID | 把 'taiwan' 視為內建源（realtimeProvider 為 'taiwan' 時 realtimeProviderName 設 null，或顯示「台灣官方資料源」），並移除 A 股時代的 'tickflow' 判斷。 | 確認 |  |
| B261 | `frontend/src/components/Layout.tsx:539` | 側邊欄分組子選單與自選頁分組狀態不同步，點同一分組連結沒有反應 | 頁內選分組時同步 setSearchParams({ group })（all 則移除參數），以網址作為唯一狀態來源。 | 確認 |  |
| B262 | `frontend/src/components/StockInfoBar.tsx:162` | 資訊條「成交量」欄位對盤中活蠟燭（整股量）沒有標示「盤中為整股量」，與日 K 資訊和報價面板的口徑標示不一致 | latest.board_lot_volume 為 true 時附加 BOARD_LOT_VOLUME_NOTE，或以 title 提示。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:120 A04-boll-atr-vol#5（部分修正，D3 決策要求標示） |
| B263 | `frontend/src/components/StockPanel.tsx:114` | 已掛載的 StockPanel 換股時，若新股票的日 K 已在快取中，dailyResult 會被父層 effect 清空，資訊條與右側分時一起消失 | 在 StockPreviewDialog 內給 StockPanel 加 key={symbol}；或者改成以 symbol 標記的 dailyResult（例如 {symbol, result}），渲染時忽略 symbol 不符的結果，不要用 effect 清空。 | 確認 |  |
| B264 | `frontend/src/components/StockPanel.tsx:185` | 分時懸停的聯動價格線用原始價，卻畫在前復權的日 K 上，選到除權息日之前的日期時位置錯誤 | 在 StockPanel 把懸停價除以所選日的 raw_close/close 比例（即 scaleToRaw 的倒數），再傳給 linkedPrice。 | 可能成立 |  |
| B265 | `frontend/src/components/StockPreviewDialog.tsx:434` | 對話框日 K 分頁右側的當日分時圖從不輪詢：StockPanel 的 refetchIntervalMs 沒有任何呼叫端傳入 | 在 :434 的 StockPanel 補上 refetchIntervalMs={intradayRefetchMs}；如果確定不輪詢是刻意設計，就移除這個 prop 並更新說明。 | 確認 |  |
| B266 | `frontend/src/components/StrategyPickPerformance.tsx:259` | 補算工作在後端重啟後消失，前端仍依舊資料的 running 狀態無限輪詢（404） | refetchInterval 改為 `query.state.status === 'error' ? false : (query.state.data?.status === 'running' ? 1500 : false)`，並對 404 關閉 retry | 確認 |  |
| B267 | `frontend/src/components/Toast.tsx:38` | 一般 Toast 與監控 AlertToast 疊在同一個角落 | 合併成同一個堆疊容器，或把一般 Toast 移到其他位置（例如 bottom-left 或 top-right）。 | 確認 |  |
| B268 | `frontend/src/components/WatchlistGroups.tsx:463` | 刪除分組時不提示綁定的監控規則會暫停；日誌說「恢復分組後自動生效」，但實際做不到 | 刪除前查出綁定該分組的規則並列在確認框中，或提供一併改綁的選項；刪除成功後讓監控規則查詢失效；修正日誌文案。 | 確認 |  |
| B269 | `frontend/src/components/WatchlistGroups.tsx:663` | 分組成員勾選、標籤拖拽排序與訊號啟用開關都沒有防連點，下一個請求依據過期的 props 計算，快速連點會丟失操作 | mutation 進行中時停用彈窗選項與拖拽；或改為樂觀更新（onMutate 先改快取，onError 回滾），並以 mutation key 讓請求依序執行。訊號開關改用 useMutation，pending 時停用。 | 可能成立 |  |
| B270 | `frontend/src/components/monitor/GenericRulesList.tsx:60` | 停用規則也走完整保存驗證：綁定的分組被刪、策略被刪或標的下市後，規則無法停用 | 提供只切換 enabled 的端點，或後端對 enabled=false 的保存略過存在性驗證；刪除分組時提示或自動停用綁定的規則。 | 確認 |  |
| B271 | `frontend/src/components/monitor/RuleEditor.tsx:326` | RuleEditor 成交量晶片以「張」顯示時四捨五入，誤報已存的門檻 | 張數保留小數（value/1000），或在已存值不是 1000 的倍數時自動切到「股」顯示。 | 確認 |  |
| B272 | `frontend/src/components/monitor/RuleEditor.tsx:659` | RuleEditor 切到「指數」後，不可見的漲跌停或分時訊號條件仍留在規則裡 | 切到指數時移除 INDEX_HIDDEN_SIGNALS 命中的 truth 條件，並提示使用者；或在 SignalPicker 顯示「不適用但已選」的訊號，讓使用者可以移除。 | 確認 |  |
| B273 | `frontend/src/components/monitor/TaiwanAlertsList.tsx:57` | 告警與報價時間用瀏覽器本地時區，七天的告警清單只顯示時分秒 | 改用 Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei' }) 格式化；非今日的告警加上 MM/DD。 | 確認 |  |
| B274 | `frontend/src/components/monitor/TaiwanAlertsList.tsx:114` | 告警記錄的股票代號按鈕仍是空點擊 | 在 TaiwanAlertsList 內直接用 useStockPreview().openPreview，與 QuotePanel、IndexBar 一致。 | 確認 | docs/code-review-2026-09-22.md C5（第 506-510 行；第七節表格 C5 仍存在） |
| B275 | `frontend/src/components/monitor/TaiwanQuotePanel.tsx:205` | C1 Monitor 報價條與指數條只靠 SSE 刷新，即時行情關閉時凍結 | 兩個 query 在即時行情關閉或 SSE 未連線時加上與詳細頁一致的 refetchInterval，或在畫面標示報價時間。 | 確認 | docs/code-review-2026-09-22.md C1（含 2026-09-24 補充） |
| B276 | `frontend/src/components/monitor/TaiwanQuotePanel.tsx:214` | 報價面板選中的標的拿不到報價時，改顯示另一檔，建立監控也用錯標的 | activeSymbol 沒有報價時顯示「此標的暫無報價」的空狀態，不要退回 quotes[0]；建立規則改用 activeSymbol。 | 可能成立 |  |
| B277 | `frontend/src/components/monitor/TaiwanQuotePanel.tsx:384` | 即時行情關閉時，監控報價條與指數條凍結，頁尾卻寫「即時行情自動同步中 (SSE)」 | 即時行情關閉時給這兩個 query 一個保守的 refetchInterval（例如 10 秒，與個股頁一致）；頁尾文案依開關與 SSE 狀態顯示。 | 確認 | docs/code-review-2026-09-22.md C1（第 440-450 行） |
| B278 | `frontend/src/components/taiwan/TaiwanReferenceData.tsx:162` | 財報區塊未標示累計期間，季累計數字看起來像單季 | 顯示 period_start 到 period_end，並把三個金額標籤改為「累計營業收入」等。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md G02-gap#2（RC17，第 244 行）；docs/audit-2026-09-24/sections/G02-gap.md:105-129；recheck-2026-10-06.md G02-gap#2（RC17）；sections/G02-gap.md G02-gap#2（recheck 判未修正） |
| B279 | `frontend/src/lib/api.ts:39` | 錯誤 toast 寫在 request() 傳輸層，同一個失敗查詢在預設重試與輪詢下會重複彈出多次 | 改由 QueryCache/MutationCache 的 onError 在最後一次失敗時彈 toast（或依 query 去重），request() 只負責拋錯；也可以對 4xx 設 retry:false。 | 確認 |  |
| B280 | `frontend/src/lib/api.ts:2625` | 多個回應型別與後端實際回傳不符（目前沒有元件讀取這些欄位） | 把各型別改成後端實際形狀：source 加上 'local_partial'、line/telegram 拿掉 ok、為 StrategyResult 另立型別（strategy_id）、intradayRefresh 改用 quoteStatus 的型別 | 確認 |  |
| B281 | `frontend/src/lib/api.ts:3339` | TaiwanBootstrapJobState 型別與後端 BootstrapJobState 不一致（downloaded_bytes／bytes_downloaded、缺 syncing 狀態） | 把 TS 欄位改成 bytes_downloaded，status union 改成後端實際會送的值（加入 syncing、移除 refreshing） | 確認 |  |
| B282 | `frontend/src/lib/format.ts:22` | 兆級金額顯示成「萬億」 | 改成「兆」。 | 確認 | recheck-2026-10-06.md A06-turnover-mcap-pct#9（RC16）；sections/A06-turnover-mcap-pct.md A06-turnover-mcap-pct#9（recheck 判未修正） |
| B283 | `frontend/src/lib/monitorBadge.ts:65` | 監控中心未讀徽標用告警檔總行數當單調計數器，後端滾動清理後少算或歸零 | 改用「最後已讀告警 ts」當游標，未讀數為 ts 大於游標的筆數（後端可提供 since 參數或回傳最新 ts），不要依賴可被 prune 的總數。 | 確認 |  |
| B284 | `frontend/src/lib/notificationSound.ts:19` | 提示音與語音播報沒有首次互動解鎖，重新整理後未點擊前告警會靜默失敗 | 在 Layout 掛一次性的 pointerdown/keydown 監聽，在手勢內建立並 resume AudioContext、呼叫 activateVoice；ctx 為 suspended 時跳過排程，並在 UI 提示「點擊以啟用聲音」。語音可保留 utterance 引用，並加逾時保護重設 _speaking。 | 可能成立 |  |
| B285 | `frontend/src/lib/queryKeys.ts:18` | 台股資料狀態與市場情報查詢繞過 QK 使用自訂 key，設定頁回補或匯入成功後的失效打不到 | 在 QK 補上 taiwanMarketIntelligence、taiwanIndustryIntelligence、taiwanAbnormalDiagnostics、taiwanScreener、taiwanStockCompare 等 key，頁面一律引用 QK.taiwanDataStatus；回補與匯入成功時一併失效市場情報類 key。 | 確認 |  |
| B286 | `frontend/src/lib/queryKeys.ts:94` | QK.alerts 只含 source，看板（limit 10）與監控中心（limit 500）共用 ['alerts',''] 快取 | 讓 QK.alerts 接收 {days, limit, source, type, extColumns} 並全部放進 key，Dashboard 改用 QK；SSE 失效仍可用前綴 ['alerts']。 | 確認 |  |
| B287 | `frontend/src/lib/signals.ts:161` | 「跌停翻紅」訊號名稱與定義不符（多數收黑），兩條路徑定義也不同 | 改名為「跌停打開／跌停回升」，或把定義改成收盤高於昨收；兩條路徑統一 | 確認 | sections/A05-limits-signals.md A05-limits-signals#7（recheck 判未修正） |
| B288 | `frontend/src/lib/theme.ts:40` | 跨分頁切換主題時，另一分頁只更新圖表調色盤，沒有切換 html.dark | 在 storage 事件處理中同步 document.documentElement.classList.toggle('dark', getTheme()==='dark')，或抽出 applyTheme() 供 setTheme 與監聽共用。 | 確認 |  |
| B289 | `frontend/src/lib/watchlist-columns.ts:79` | 自選與資訊列的財務欄位仍走 A 股的 financial 能力 | 台股部署隱藏這些欄位，或改接台股官方財報。 | 確認 | recheck-2026-10-06.md G02-gap#1（RC22）；sections/G02-gap.md G02-gap#1（recheck 判未修正） |
| B290 | `frontend/src/lib/watchlist-columns.ts:128` | 欄位設定寫入後端失敗時只存在 localStorage，但下次載入會用後端舊值覆蓋本機的新值 | 存設定時附上 updated_at，載入時比較兩邊取較新者；或在 PUT 失敗時標記 dirty，下次載入先把本機值回寫後端。 | 確認 |  |
| B291 | `frontend/src/main.tsx:19` | 全域 401 攔截用子字串 '401' 判斷，錯誤訊息裡的代號含 401 就被踢到登入頁 | 讓 request() 拋出帶 status 與 code 的錯誤類別，攔截器只看 status===401，或 403 且 code==='NOT_INITIALIZED'。 | 確認 |  |
| B292 | `frontend/src/pages/Auth.tsx:50` | 首次設定密碼後沒有建立 session，按「設置並進入」會被踢回登入頁；authStatus 失敗時誤顯示設定表單 | setup 成功後自動登入（後端發 session，或前端接著呼叫 authLogin）；authStatus 失敗時顯示錯誤與重試，不要假設未設定。 | 確認 |  |
| B293 | `frontend/src/pages/Dashboard.tsx:69` | 看板監控小組件與監控中心共用同一個 alerts 查詢鍵，但 limit 不同 | 讓 QK.alerts 接受 limit／days 參數，兩頁都改用 QK，避免硬寫字面量 key。 | 確認 |  |
| B294 | `frontend/src/pages/Dashboard.tsx:227` | 盤後排程發佈與晚間定稿後，開著的看板與選股頁不會自動更新 | 在資料發佈或 pipeline 完成時發 SSE 事件，或在 pipelineJobs 由進行中變為完成時，失效台股狀態、情報與選股相關的 key。 | 確認 |  |
| B295 | `frontend/src/pages/Dashboard.tsx:453` | 策略推薦卡標頭把各策略選股數相加後以「檔」顯示，重複計算 | 改以 symbol 去重計數，或把文案改為「N 筆入選」。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md A12-screener-stats#6（:218）；recheck-2026-10-06.md A12-screener-stats#6；sections/A12-screener-stats.md A12-screener-stats#6（recheck 判未修正） |
| B296 | `frontend/src/pages/Dashboard.tsx:563` | 看板「還有 N 個策略有選股結果 →」連到沒有策略結果的選股器頁 | 連結改成 /strategies。 | 確認 |  |
| B297 | `frontend/src/pages/Dev.tsx:45` | /dev 分鐘K探測在正式版開放，日期用 UTC，部分資料也算「有」 | 日期改用本地格式（lib/format 的 fmtDate）並跳過非交易日；以 source === 'local' 才算齊全；正式版以 import.meta.env.DEV 或權限開關隱藏 /dev。 | 確認 |  |
| B298 | `frontend/src/pages/Monitor.tsx:138` | 觸發記錄載入中或讀取失敗時顯示成「沒有記錄」 | 分別渲染 loading／error／empty 三種狀態；錯誤時不要把 total 當成 0 來停用操作。 | 確認 | docs/code-review-2026-09-22.md C5 |
| B299 | `frontend/src/pages/Onboarding.tsx:61` | 引導完成標記寫入失敗時的「仍放行」被路由守衛擋回，使用者回到第一步 | 失敗時用 setQueryData 暫時放行，或在守衛加一個 session 內的略過旗標，並提示標記未寫入。 | 確認 |  |
| B300 | `frontend/src/pages/Strategies.tsx:90` | 策略庫沒有處理快取結果的載入與失敗狀態，所有策略都被標成「未執行」並附上錯誤原因 | cached 載入中時顯示讀取狀態、失敗時顯示錯誤並提供重試，不要把空的 results 當成「本輪沒有執行」。 | 確認 |  |
| B301 | `frontend/src/pages/StrategyBacktest.tsx:300` | 回測明細的 exit_reason 直接顯示原始訊號 ID | 改成 cnSignal(trade.exit_reason)。 | 確認 | README「第 4 階段的已知缺口與延後項目」 |
| B302 | `frontend/src/pages/TaiwanScreener.tsx:137` | AI 解析可經由 Enter 重複送出，較慢的舊回應會覆蓋新結果 | handleTranslate 開頭加上 `if (nlTranslating) return`，或改用 useMutation 並只採用最後一次請求的結果。 | 確認 |  |
| B303 | `frontend/src/pages/TaiwanScreener.tsx:263` | 選股條件每打一個字就重跑一次全市場掃描，結果表隨之閃爍 | 數字輸入改用 debounce（約 300ms）或失焦／按 Enter 才送出，加上 placeholderData: keepPreviousData，並把 signal 傳給 request。 | 確認 |  |
| B304 | `frontend/src/pages/TaiwanScreener.tsx:275` | 選股頁資料狀態使用平行查詢鍵 ['taiwanDataStatus']，資料匯入後的失效打不到 | 選股頁改用 QK.taiwanDataStatus，並把台股選股與情報類 key 收進 queryKeys.ts。 | 確認 |  |
| B305 | `frontend/src/pages/TaiwanScreener.tsx:737` | 點擊產業名稱套用的產業篩選在表單中看不到，也無法單獨清除 | 在篩選面板顯示目前產業的 chip 並提供 ✕ 清除，或改成產業下拉選單。 | 確認 |  |
| B306 | `frontend/src/pages/TaiwanScreener.tsx:990` | 異常訊號標籤寫死在 JSX，嚴重度切點與 evidence 欄位語意不一 | 標籤改由 ABNORMAL_SIGNAL_DEFINITIONS 產生；baseline 欄位統一語意。 | 確認 | recheck-2026-10-06.md G01-gap#6、G01-gap#7（RC17） |
| B307 | `frontend/src/pages/TaiwanStockCompare.tsx:137` | null 一律顯示「N/A（不適用）」，個股基本面寫死 unavailable | 區分「不適用」與「無資料」；接上 TaiwanFundamentalStore。 | 確認 | recheck-2026-10-06.md G02-gap#0、G03-gap#9（RC34）；sections/G03-gap.md G03-gap#9（recheck 判未修正） |
| B308 | `frontend/src/pages/TaiwanStockDetail.tsx:49` | 日K區間按鈕標的是自然期間，實際送出的是交易日根數（「1 年」約 1.5 年） | 按鈕改送交易日數（約 21／63／126／245 根），或把標籤改成「N 根」。 | 確認 |  |
| B309 | `frontend/src/pages/TaiwanStockDetail.tsx:121` | 個股頁同時有兩套成交量單位：圖表用全站設定，盤面、五檔與籌碼用頁內預設「張」 | 頁內改用 useVolumeUnit() 與 formatVolume，移除本頁的 volUnit state。 | 確認 | docs/audit-2026-09-24/sections/G05-gap.md G05-gap#7 驗證第 7 點（同頁口徑不一致）；recheck-2026-10-06.md G05-gap#7 標為未修正 |
| B310 | `frontend/src/pages/TaiwanStockDetail.tsx:289` | 比較清單已滿 5 檔時按「加入比較」會靜默丟掉當前代號 | 已滿時提示使用者先移除，或把當前代號放到第一位並擠掉最舊的一檔。 | 確認 |  |
| B311 | `frontend/src/pages/TaiwanStockDetail.tsx:395` | 背景輪詢失敗時「代碼不符規範」的錯誤卡與舊資料同時出現，toast 每輪都彈 | 有 data 時只顯示輕量的「更新失敗」提示，不用初次載入失敗的文案；輪詢請求加 quiet 或對 toast 去重。 | 確認 |  |
| B312 | `frontend/src/pages/TaiwanStockDetail.tsx:629` | 五檔賣盤不足 5 檔時標號錯位，最佳賣價被標成「賣3」之類 | 改用 {asks.length - idx}。 | 確認 |  |
| B313 | `frontend/src/pages/TaiwanStockDetail.tsx:785` | 「近 5 日指標」卡混放單日的融資變動與當日券資比 | 改成 5 日累計，或為各欄分別標示期間。 | 確認 | recheck-2026-10-06.md A07-chips#6（RC17）；sections/A07-chips.md A07-chips#6（recheck 判未修正） |
| B314 | `frontend/src/pages/TaiwanStockDetail.tsx:905` | 「近 7 日」警報列表只顯示時分秒，看不出觸發日期 | 顯示 MM-DD HH:mm:ss；後端改用台北時區的 aware datetime。 | 確認 |  |
| B315 | `frontend/src/pages/TaiwanStockDetail.tsx:942` | 研究證據卡使用未定義的 text-up／text-down，報酬與乖離沒有漲跌色 | 改用 text-bull／text-bear，或與同頁一致的 text-rose-500／text-emerald-500（可用 signColorClass）。 | 確認 |  |
| B316 | `frontend/src/pages/Watchlist.tsx:696` | 頁內切換分組不會更新 URL，側欄高亮錯誤，且點同一個分組連結沒有反應 | handleGroupSelect 與新建、刪除分組時都以 replace 方式同步 setSearchParams({group})，讓 URL 成為選取狀態的唯一來源。 | 確認 |  |
| B317 | `frontend/src/pages/Watchlist.tsx:1579` | 背景重新取得失敗時，以錯誤訊息取代仍有效的快取資料 | 改為「有 data 就顯示 data」，只在 isLoadingError（沒有資料）時顯示錯誤；有資料又刷新失敗時，另外加一個不遮蔽內容的提示。 | 確認 |  |
| B318 | `frontend/src/pages/Watchlist.tsx:1813` | 自選頁預設顯示的「訊號」欄對台股永遠是空的（連板徽章亦同） | 在 _TAIWAN_INDICATOR_FIELDS 一併讀取已發佈的 signal_* 與 consecutive_limit_*；若刻意不提供，就在台股場景隱藏這些欄位與徽章，或明確標示「無資料」。 | 確認 |  |
| B319 | `frontend/src/pages/settings/DataSourceEditor.tsx:135` | 「新增資料來源」沒有檢查名稱衝突，同名時直接覆寫既有自訂來源的設定檔 | 新建模式下比對 sources.data 的 builtin、plugins、custom 名稱並阻擋，也擋保留字 taiwan、tickflow、__new__。後端 POST 區分 create 與 update，名稱衝突時回 409。 | 確認 |  |
| B320 | `frontend/src/pages/settings/DataSourceEditor.tsx:262` | DataSourceEditor 資料集清單把 Toggle 按鈕巢狀放在另一個 <button> 內（無效 HTML，React 會告警） | 兩個做法擇一。(1) 外層列改成 <div role="button" tabIndex={0}>，並自行處理 Enter/Space。(2) 把 Toggle 移出列按鈕，與列按鈕並列成兄弟元素。另外給 Toggle 加 aria-label，例如 `${DATASET_LABEL[key]} 啟用`。 | 確認 |  |
| B321 | `frontend/src/pages/settings/DataSourceEditor.tsx:342` | 自訂資料來源編輯器仍以 A 股代碼當預設測試標的和欄位說明 | 預設測試標的改成 2330.TWSE,8069.TPEX，說明改為台股代碼格式，「回退 TF」改為「回退內建」，並同步更新 docs/custom-data-source.md。 | 確認 |  |
| B322 | `frontend/src/pages/settings/DataSourceEditor.tsx:623` | 欄位對應編輯器的列 id 可能重複，新增的列會和既有列連動 | nextId 的初始值改為 max(rows.id)+1。 | 確認 |  |
| B323 | `frontend/src/pages/settings/DataSources.tsx:681` | 內建「台灣官方資料源」卡片的「財務」標籤顯示兩次 | 附加前先去重，或只在不含 financial 時才附加。 | 確認 |  |
| B324 | `frontend/src/pages/settings/JobTimeoutCard.tsx:58` | 「逾時設定」分頁儲存的兩個停滯逾時值，沒有任何生產程式使用 | 移除這個分頁和對應的 API 與偏好，或者把台股長任務接上同一套停滯偵測。 | 確認 |  |
| B325 | `frontend/src/pages/settings/MenuSettings.tsx:238` | 選單隱藏與預設推播管道用舊快照算出整份清單，快速連點會互相覆蓋 | 用 setQueryData 做樂觀更新，並以函式式 prev 計算下一份清單；或在 mutation 進行中停用其他切換。 | 可能成立 |  |
| B326 | `frontend/src/pages/settings/System.tsx:222` | 系統設定的語音音色一旦手選過，就無法回到「預設偏好」 | 「預設偏好」選項的 value 改用 ''，回顯時依 voiceConfigured 判斷要選哪一項。 | 確認 |  |
| B327 | `frontend/src/pages/settings/TaiwanEnrichmentDataCard.tsx:91` | C4 啟動新回補時進度區沿用上一輪 activeJob（部分修復） | onSuccess 內 setActiveJob(null)，進度區在 activeJob 為 null 時顯示啟動中的狀態。 | 確認 | docs/code-review-2026-09-22.md C4（2026-09-24 補正後的殘留問題） |
| B328 | `frontend/src/pages/settings/TaiwanHistoryDataCard.tsx:102` | 資料包下載與籌碼回補的進度輪詢遇到 404 會無限重試，並每 2 秒彈一次錯誤 toast | 404（任務不存在）當成終態：停止輪詢、清掉 activeJobId 與 activeJob、提示「任務已遺失」，並 refetch status。其他暫時性錯誤改用 quiet 請求，並設重試上限。 | 確認 |  |
| B329 | `frontend/src/pages/settings/TaiwanHistoryDataCard.tsx:254` | 「更新到最新」的狀態只存在元件裡，切換分頁或來源後可以再觸發一次並與進行中的更新並行；更新期間「重新下載資料包」也沒有停用 | 把「更新到最新」改成後端單飛的背景任務，已有任務時回傳既有 job_id；前端掛載時先查詢進行中的任務。最少要在 254 行的條件加入 updateLatestMutation.isPending。 | 確認 |  |
| B330 | `frontend/src/router.tsx:85` | 路由沒有 404 與 errorElement，打錯網址或 lazy chunk 載入失敗時出現 React Router 預設錯誤頁 | 在根路由加 errorElement（處理 404 與 chunk 失敗並提供重新整理），並在 Layout children 加 { path: '*' } 的站內 404 頁。 | 確認 |  |
| B331 | `frontend/tailwind.config.ts:28` | tailwind 字型堆疊優先用簡中字型，macOS 上繁體字以簡中字形顯示 | 改為 'PingFang TC'、'Noto Sans TC'、'Microsoft JhengHei' 等繁中字型。 | 可能成立 |  |

### 台股資料與分析（43 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B176 | `backend/app/taiwan/abnormal_diagnostics.py:308` | 異常診斷的市場 advance_ratio 含 ETF，產業 advance_ratio 只含股票 | 市場比例改用 by_instrument.stock，或在欄位名稱與說明中標明母體。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:238 G01-gap#8（未修正）；sections/G01-gap.md G01-gap#8（recheck 判未修正） |
| B177 | `backend/app/taiwan/abnormal_diagnostics.py:333` | 法人 20 日基期以有列的日數為分母，與 T86 缺列視為 0 的慣例不一致 | T86 已公布日的缺列補 0 後再計算 | 確認 | sections/G01-gap.md G01-gap#10（recheck 判未修正） |
| B178 | `backend/app/taiwan/abnormal_diagnostics.py:424` | 異常訊號門檻比較前先四捨五入，略低於門檻的值會被判定觸發 | 用未捨入的值比較門檻，只在寫入 evidence 時才捨入。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:235 G01-gap#5（未修正）；sections/G01-gap.md G01-gap#5（recheck 判未修正）；sections/G01-gap.md G01-gap#7（recheck 判未修正） |
| B179 | `backend/app/taiwan/abnormal_diagnostics.py:575` | 異常診斷的門檻、基期與母體細節（先捨入再比、自身納入產業平均、母體不一致、沒有融資基數門檻） | 用原值比門檻；產業平均排除自身（leave-one-out）；兩處母體一致；加上最低融資餘額。 | 確認 | recheck-2026-10-06.md G01-gap#5（RC32）、G01-gap#8、G01-gap#9、G01-gap#11（RC26）；sections/G01-gap.md G01-gap#11（recheck 判未修正） |
| B180 | `backend/app/taiwan/ai_research.py:142` | 16:30～21:40 融資未公布時，AI 研究排除大盤脈絡 | 待使用者決定：帶入並加註，或維持排除並在畫面說明。 | 可能成立 | recheck-2026-10-06.md G03-gap#6（RC34）；README「需要先決定的事項」第 1 點；sections/G03-gap.md G03-gap#6（recheck 判未修正） |
| B181 | `backend/app/taiwan/ai_research.py:376` | AI 研究與多股比較每次請求都重算市場、產業快照與全市場診斷 | 注入共用的 request-scoped snapshot（可沿用 comparison 的 _MemoizingSnapshotProxy）；診斷提供只計算指定 symbols 的入口；刪除 df_p20_daily。 | 確認 | 沒有被記錄 |
| B182 | `backend/app/taiwan/bootstrap.py:512` | Bootstrap 在 daemon 執行緒以 shutil.copy2 直接寫進正式分區路徑，重載或退出時中斷會留下截斷檔而且不再補 | 先複製到同目錄的 .tmp 再 os.replace；或者先完整解壓到暫存的 daily 目錄、驗證後逐檔原子搬移；啟動時檢查並刪除無法讀取的分區。 | 可能成立 |  |
| B183 | `backend/app/taiwan/bootstrap.py:545` | Bootstrap 匯入完成後沒有發佈 enriched、沒有刷新記憶體快取，前端也只失效狀態查詢 | 匯入成功後走與 update_to_latest 相同的發佈與快取刷新流程（或直接呼叫 update_to_latest），前端在成功時 invalidateQueries()。 | 確認 |  |
| B184 | `backend/app/taiwan/bootstrap.py:602` | 盤後排程與「更新到最新」、多分頁重複觸發之間沒有任何互斥，同一批分區與 enriched 發佈會並行改寫 | 在 run_update 與 update_to_latest 外層加一把模組級、可非阻塞嘗試的更新鎖（同進程用 threading.Lock，跨進程沿用 _exclusive_generation_lock 的檔案鎖模式）；手動端拿不到鎖時回 409「排程更新中」。caches_refreshed 改成依兩個 helper 的實際結果設定。 | 可能成立 |  |
| B185 | `backend/app/taiwan/bootstrap.py:664` | update_to_latest 只有重抓失敗、沒有新抓到日期時直接拋錯，排在後面的籌碼補齊與指標發佈都不會執行 | 把 _refresh_chip_data 移到 raise 之前，或在只有 repair 失敗時改成回傳部分成功；並補上對應測試。 | 確認 |  |
| B186 | `backend/app/taiwan/comparison_ai_research.py:358` | AI 報告的排序與投資建議用語防線不一致：missing_information 不受檢查，單股報告完全沒有執行期檢查 | missing_information 也納入掃描；單股報告套用同一份 blocklist，並加入推薦與目標價相關詞，命中時回 invalid_output。 | 可能成立 | 沒有被記錄。docs/market-filter-volume-patterns-plan.md D1 說 AI 輸出驗證這次不動 |
| B187 | `backend/app/taiwan/daily_store.py:192` | 台股日線分區、部分窗口 enriched 發佈與股本檔的原子替換沒有 Windows 重試，被讀者開著時直接失敗 | 三處都改用 enriched_generation._replace_with_retry 或 universe.service.write_atomically；share_capital 改用唯一暫存檔名。 | 可能成立 | docs/code-review-2026-09-22.md:814 只記錄 DuckDB fetchone 的檔案鎖修正，沒有涵蓋這三個寫入點 |
| B188 | `backend/app/taiwan/daily_store.py:242` | read_latest_per_symbol 每次讀完整段歷史的所有分區才取各檔最新一列，選股每次請求約 2 秒，且隨歷史線性增長 | 改用 scan_parquet 的 lazy group_by，或只讀最近 N 個分區；停牌超過 N 日的標的另行處理。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:158（A07-chips#4 記的是 read_latest_per_symbol 的語意問題，不是效能） |
| B189 | `backend/app/taiwan/daily_update.py:451` | 資料狀態的融資融券以 16:00 為目標日，每個交易日 16:00–21:40 都誤報「待更新」，排程時間寫死為 16:30 | margin 的狀態改用 resolve_margin_target(target, now) 當目標日；排程時間改為回傳兩班的實際時間。 | 確認 |  |
| B190 | `backend/app/taiwan/detail_service.py:412` | 法人與融資「缺列」被當成沿用前一日或最新一筆 | 明定缺列等於 0 的語意：選股器只取目標日的列，缺列為 0；詳細頁標示資料日期。 | 確認 | recheck-2026-10-06.md A07-chips#3、A07-chips#4、G01-gap#10（RC33）；sections/A07-chips.md A07-chips#3（recheck 判未修正） |
| B191 | `backend/app/taiwan/detail_service.py:767` | 個股詳情的最近警報先取全市場前 200 筆才過濾個股，alert_id 也恆為空 | 先過濾 symbol 再截斷筆數；alert_id 以 ts 加 rule_id 合成；trigger_price 改為 Optional，遇到 None 時直接回 None。 | 確認 | 沒有被記錄 |
| B192 | `backend/app/taiwan/enriched_refresh.py:190` | 部分窗口 enriched 發佈沒有更新 generation，以 generation 為鍵的已發佈日K快取在同日維持舊值 | 部分窗口發佈也包在 EnrichedPublication（begin/write_parquet/commit）裡，或在寫完後呼叫 bump_enriched_generation；同時讓寫入取得 Windows 重試。 | 確認 | docs/audit-2026-09-24/README.md:206 記錄 10-09 21:40 部分窗口重新發佈後 .matrix_generation_stock.json 沒有改，沒有列為缺陷 |
| B193 | `backend/app/taiwan/enriched_refresh.py:512` | 「跌停翻紅」的三處定義不一致，也都不要求收盤高於昨收 | 三處統一定義；若保留「翻紅」的名稱，就加上 raw_close > 前一日原始收盤（或參考價）的條件，否則改名為「跌停打開」。 | 確認 | recheck-2026-10-06.md A05-limits-signals#7（RC17）；README P2 第 10 點 |
| B194 | `backend/app/taiwan/enrichment/factors.py:18` | 籌碼因子程式只被測試使用，且券資比在融資為 0 時給 0 | 刪除，或接到 detail_service 並修正 0/0 的語意。 | 確認 | recheck-2026-10-06.md A07-chips#5；sections/A07-chips.md A07-chips#5（recheck 判未修正） |
| B195 | `backend/app/taiwan/enrichment/index.py:86` | parse_taiex_rows 把第一列的昨收設為開盤價（潛在） | 首列不輸出漲跌或另取前月最後收盤 | 可能成立 | sections/G03-gap.md G03-gap#8（recheck 判未修正） |
| B196 | `backend/app/taiwan/enrichment/institutional.py:49` | N4 法人與融資 parse_payload 以請求日期標記資料，不比對回應自身日期 | 比照 market_institutional 的 _stamped_for，回應 date 與請求不符時回空列。 | 可能成立 | docs/code-review-2026-09-22.md N4；同類 docs/audit-2026-09-24 A01-price-data#7（已修於 snapshot_provider） |
| B197 | `backend/app/taiwan/enrichment/institutional.py:71` | 法人 adapter 分不出「當日 T86 沒有這檔」和「當日沒有 T86」，詳細頁因此退回顯示前一交易日的法人資料 | parse_payload 回傳「表格存在與否」的旗標；表格存在但沒有該股時，回傳當日全 0。 | 確認 | docs/audit-2026-09-24/sections/A07-chips.md A07-chips#3（recheck-2026-10-06.md:157 標未修正） |
| B198 | `backend/app/taiwan/enrichment_backfill.py:127` | 三大法人與融資融券回補任務沒有套用融資融券公布時間上限，可能存下只有單一交易所的融資融券日 | 融資融券段落的 end 改用 resolve_margin_target(end_date, taipei_now(), calendar)。 | 可能成立 |  |
| B199 | `backend/app/taiwan/enrichment_backfill.py:251` | 融資 21:40 公布門檻只套用在「更新到最新」，籌碼回補與 CLI 仍會在公布前抓當日融資 | enrichment_backfill 的融資終點改用 resolve_margin_target(target, taipei_now(), calendar)；run_update 也可以在 refresh_margin 時預設套用同一門檻（21:40 排程剛好在門檻上，不受影響）。 | 可能成立 |  |
| B200 | `backend/app/taiwan/etf_data.py:178` | ETF 官方資料每次呼叫都新建 httpx.Client 且從不關閉，profile 與 snapshot 各抓一次完整的 ETF 清單 | 共用模組層級的 client，或用 with 關閉；同一請求只下載一次清單，再分給 profile 與 snapshot。 | 確認 |  |
| B201 | `backend/app/taiwan/fundamentals.py:223` | 基本面抓取沒有快取與重試，任一類別出錯就整卡失敗 | 以 (host, path) 為鍵做 TTL 快取並共用 client；把類別迴圈的網路例外和找不到列分開處理。 | 確認 | recheck-2026-10-06.md G02-gap#4、G02-gap#5（RC20）；sections/G02-gap.md G02-gap#4（recheck 判未修正） |
| B202 | `backend/app/taiwan/industry_intelligence.py:463` | RELATIVE_STRENGTH_OUTLIER 的產業等權報酬包含個股自身 | 以排除自身的 leave-one-out 平均比較 | 可能成立 | sections/G01-gap.md G01-gap#9（recheck 判未修正） |
| B203 | `backend/app/taiwan/institutional_store.py:212` | 選股器每次請求都經 read_latest_per_symbol 讀取全部法人與融資分區，成本隨歷史線性成長，而且沿用每檔自己的舊列 | 改讀最新分區（read_latest_date_rows 已存在但未使用），缺列補 0，或加上以分區 mtime 為鍵的快取。 | 確認 | docs/audit-2026-09-24/sections/A07-chips.md A07-chips#4（舊列語意，recheck 標未修正；效能部分文件未記錄） |
| B204 | `backend/app/taiwan/market_intelligence.py:420` | 市場強弱的成交家數與漲跌家數口徑：無成交列、平盤與指數 | 有成交改以 close 非 null 且 volume>0 判定；close 為 null 的列歸入 uncompared；平盤獨立成一段；指數改讀本機 index_daily。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:221 A12-screener-stats#9（未修正） |
| B205 | `backend/app/taiwan/market_intelligence.py:749` | 市場統計的指數狀態寫死為 unavailable，漲跌分布與成交家數口徑有誤 | 讀本機 TAIEX；另設「平盤」一段。 | 可能成立 | recheck-2026-10-06.md A12-screener-stats#9（RC15）；sections/A12-screener-stats.md A12-screener-stats#9（recheck 判未修正） |
| B206 | `backend/app/taiwan/market_rules.py:255` | 升降單位表沒有逐列日期維度（部分修正） | Polars 介面改接受日期欄位表達式，依每列 date 選表 | 可能成立 | sections/A05-limits-signals.md A05-limits-signals#14（recheck 部分修正） |
| B207 | `backend/app/taiwan/providers/hybrid_provider.py:60` | 官方基本面沒有快取，每開一次個股頁重新下載多份全市場資料 | 以 (host, path) 為鍵加 TTL 快取並共用 client | 確認 | sections/G02-gap.md G02-gap#5（recheck 判未修正） |
| B208 | `backend/app/taiwan/providers/hybrid_provider.py:127` | 逐檔日K的官方月表失敗時，整檔改用 FinMind／Yahoo，寫入日線 store 時丟掉來源標記；Yahoo 的價量是分割調整後的值，也沒有成交金額 | 寫入 store 的路徑只接受官方來源（status 不是 third_party*），否則記為失敗；官方月表加間隔與重試；Yahoo 標成 split-adjusted，或不再作為日K備援。 | 可能成立 |  |
| B212 | `backend/app/taiwan/research_context.py:451` | 研究脈絡的 20 日高低以收盤價計，沒有標示口徑 | 改名為 close_high_20d，或在 payload 註明口徑。 | 可能成立 | recheck-2026-10-06.md G03-gap#5（RC17）；sections/G03-gap.md G03-gap#5（recheck 判未修正） |
| B213 | `backend/app/taiwan/research_context.py:557` | 研究脈絡的法人 5 日累計取「最後 5 列」而非最後 5 個交易日 | 改用 inst_available[-5:] 的日期集合篩選後加總（缺列視為 0），coverage_days_5d 改為有列的交易日數，口徑與 detail_service._institutional_5d 對齊。 | 確認 | docs/code-review-2026-09-22.md 三、次要問題第 2 條（research_context inst_sorted.tail(5)）；相關 docs/audit-2026-09-24/README.md RC33 |
| B214 | `backend/app/taiwan/research_context.py:706` | 比較頁個股本益比永遠顯示「N/A（不適用）」 | 接上 TaiwanFundamentalStore 的 valuation，或顯示「未取得」 | 確認 | sections/G02-gap.md G02-gap#0（recheck 判未修正） |
| B215 | `backend/app/taiwan/research_context.py:815` | 研究脈絡的證據計數與即時狀態寫死 | 依實際取得的欄位計數，並納入單一標的缺價。 | 確認 | recheck-2026-10-06.md G03-gap#7（RC34）；sections/G03-gap.md G03-gap#7（recheck 判未修正） |
| B216 | `backend/app/taiwan/screener.py:433` | 選股器以每檔自己的最新法人／融資列篩選，5 日欄位恆為 null | 只取目標日的列（缺列依 B9 視為 0 或標缺），並實作 5 日加總 | 確認 | sections/A07-chips.md A07-chips#4（recheck 判未修正） |
| B217 | `backend/app/taiwan/screener_nl.py:190` | 自然語言選股與選股 API 缺少 LLM 輸出的型別驗證與數值範圍驗證 | 解析後先檢查 dict 與 list 型別，不符時轉成 clarification。在請求模型加 Field 範圍（例如 change_pct 介於 ±1、rsi 介於 0～100）。signals 以 signal_ 前綴或內建訊號清單做白名單驗證。 | 確認 | 沒有被記錄 |
| B218 | `backend/app/taiwan/share_capital_store.py:204` | 股本刷新時，某交易所回傳空陣列或欄位改名會被當成成功，該交易所原有的分母整批被丟掉 | _fetch 解析出 0 列時視為該交易所失敗（拋錯或回傳 failed），沿用舊列；把測試改用不會觸發這條路徑的資料。 | 可能成立 |  |
| B219 | `backend/app/taiwan/watchlist_enrichment.py:122` | ETF 的換手率與市值為 null，畫面顯示「—」而不是「不適用」 | 後端回傳 not_applicable 原因，前端顯示「不適用」。 | 確認 | recheck-2026-10-06.md A06-turnover-mcap-pct#8（RC29，延到第 5 階段）；sections/A06-turnover-mcap-pct.md A06-turnover-mcap-pct#8（recheck 未修正，延到第 5 階段） |
| B220 | `backend/app/taiwan/watchlist_enrichment.py:128` | 盤中到官方日K發佈前，換手率分子為 MIS 整股量（部分修正，依決策 D3 標示） | 同 A04#5，取得零股量後併入，或在盤中改以同口徑分母 | 確認 | sections/A06-turnover-mcap-pct.md A06#3；sections/A13-realtime.md A13#3（recheck 部分修正） |
| B221 | `backend/app/taiwan/watchlist_enrichment.py:394` | 盤中量比分子為 MIS 整股量、分母為官方全日量（部分修正，依決策 D3 保留並標示） | 若要完全修正，需取得 MIS 零股量（ov）併入分子，或分母改用同口徑整股量 | 可能成立 | sections/A04-boll-atr-vol.md A04-boll-atr-vol#5（recheck 部分修正） |

### 策略、指標與監控（32 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B118 | `backend/app/indicators/levels.py:352` | 價位標籤為簡體中文，直接顯示在繁中 UI 與提醒名稱 | 後端改為繁中標籤或前端以 type 對照 | 確認 | sections/G04-gap.md G04-gap#10（recheck 判未修正） |
| B119 | `backend/app/indicators/levels.py:367` | Keltner 三檔不是原作者定義 | 改用 EMA 與 ATR10，或在標籤註明自訂參數 | 確認 | sections/G04-gap.md G04-gap#5（recheck 判未修正） |
| B120 | `backend/app/indicators/levels.py:399` | 上市不到約 15 個交易日時，/levels 所有價位都變成空 | 先用 _ok() 檢查原始值再轉 float；compute_levels 改成逐組 try，一組失敗不影響其他組 | 確認 |  |
| B121 | `backend/app/indicators/levels.py:543` | 關鍵價位不依台股升降單位取整，整數關口步長沿用 A 股設計 | 以 TickSizeModel 依方向取整；步長依台股檔位設計 | 確認 | sections/G04-gap.md G04-gap#7（recheck 判未修正） |
| B122 | `backend/app/indicators/levels.py:650` | 關鍵價位的細節：Keltner 定義、沒有依升降單位取整、聚合會鏈式漂移、簡體標籤、未使用的輸出 | 依升降單位取整；聚合改以群組平均或最大成交量代表；標籤改繁中；移除沒有用到的 series。 | 確認 | recheck-2026-10-06.md G04-gap#5、G04-gap#7、G04-gap#8、G04-gap#9（RC21）、G04-gap#10（RC16） |
| B123 | `backend/app/indicators/levels.py:658` | _aggregate_levels 註解說保留最新，實際保留價格最高者且會鏈式漂移 | 以群組起點或平均值比較，並修正註解 | 確認 | sections/G04-gap.md G04-gap#8（recheck 判未修正） |
| B124 | `backend/app/indicators/pipeline.py:188` | high_60d 以收盤價計卻標成「60日最高价」，trend_breakout 的 MA60 條件也多餘 | 標籤改成「60日收盤最高」。 | 確認 | recheck-2026-10-06.md A08-strategies-trend#7（RC17） |
| B125 | `backend/app/indicators/pipeline.py:333` | 漲跌皆為 0 時 RSI 給 0（應為中性） | avg_gain 與 avg_loss 都是 0 時另行定義（例如 50 或 null）。 | 確認 | README「第 4 階段的已知缺口與延後項目」（漲跌都是 0 時 RSI 給 0） |
| B126 | `backend/app/indicators/pipeline.py:571` | 布林通道仍用樣本標準差（ddof=1） | 兩條路徑都改成 ddof=0，提升 INDICATOR_DEFINITION_VERSION 觸發全量重新發佈。 | 確認 | recheck-2026-10-06.md A05-limits-signals#10、A08-strategies-trend#6、A09-strategies-momentum#8、G04-gap#6（RC30）；README 第 4 階段延後項目；sections/A05-limits-signals.md A05#10；A08-strategies-trend.md A08#6；A09-strategies-momentum.md A09#8；G04-gap.md G04#6（皆 recheck 判未修正） |
| B127 | `backend/app/indicators/pipeline.py:598` | 「60日新高」實為收盤不低於含當日的 60 根收盤最高，標成「60日最高价」；MA60 條件多餘 | 改名為「60日收盤最高」或改用前 60 日（shift(1)）高點；移除或說明 MA60 條件 | 確認 | sections/A08-strategies-trend.md A08-strategies-trend#7（recheck 判未修正） |
| B128 | `backend/app/indicators/pipeline.py:619` | 指標口徑仍是 A 股或樣本慣例：macd_hist=2×(DIF−DEA)、布林用樣本標準差、年化波動用簡單報酬×√252 | 依決策定案後一併修改 pipeline 與 matrix，並提高 INDICATOR_DEFINITION_VERSION 觸發全量重新發佈 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md：A02-ma-macd#4（RC16）、A08-strategies-trend#6／A05-limits-signals#10（RC30）、A04-boll-atr-vol#8／A09-strategies-momentum#10（RC31），皆未修正；recheck-2026-10-06.md A02-ma-macd#4（RC16）；sections/A02-ma-macd.md A02-ma-macd#4（recheck 判未修正） |
| B129 | `backend/app/indicators/pipeline.py:626` | 價格完全不動的窗口：KD 因 RSV 0/0 變 null，RSI 在完全不動的序列上得 0 | 明確定義 H9==L9 時的 RSV（例如沿用前值或 50），avg_gain=avg_loss=0 時 RSI 回傳 null 或 50；pipeline 與 matrix 兩條路徑一起修改，並提高 INDICATOR_DEFINITION_VERSION | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md：A03-kd-rsi#6（RC32，P3，未修正；RSI=0 的部分是本次新發現）；recheck-2026-10-06.md A03-kd-rsi#6（RC32；文件仍寫未修正、兩條路徑不同，已過時）；sections/A03-kd-rsi.md A03-kd-rsi#6（recheck 判未修正，本次改判部分修正） |
| B130 | `backend/app/indicators/pipeline.py:682` | annual_vol_20d 以簡單報酬、ddof=1、√252 年化 | 依第 5 階段的決策改成對數報酬與既定年化日數，兩條路徑一起改。 | 確認 | recheck-2026-10-06.md A04-boll-atr-vol#8、A09-strategies-momentum#10（RC31）；sections/A04-boll-atr-vol.md A04-boll-atr-vol#8；sections/A09-strategies-momentum.md A09-strategies-momentum#10（皆 recheck 判未修正） |
| B131 | `backend/app/indicators/pipeline.py:769` | 均線與 MACD 交叉判定沒有浮點容差 | 比較前先依價格精度取整，或加 epsilon 容差。 | 確認 | recheck-2026-10-06.md A02-ma-macd#8、A05-limits-signals#12（RC32）；sections/A02-ma-macd.md A02-ma-macd#8；sections/A05-limits-signals.md A05-limits-signals#12（皆 recheck 判未修正） |
| B132 | `backend/app/indicators/pipeline.py:1080` | signal_limit_down_recovery 有兩套定義：pipeline 要求收陽，台股發佈不要求 | 台股列一律走 taiwan_limit_signals／add_taiwan_limit_signals；發佈端呼叫 compute_limit_signals 時只要求 needed={'turnover_rate'} | 可能成立 | docs/audit-2026-09-24/recheck-2026-10-06.md：A05-limits-signals#7（RC17，P2，未修正） |
| B159 | `backend/app/strategy/ai_generator.py:3` | ai_generator.py 模組 docstring 說讀取 prompts/strategy-guide.md，實際載入的是 strategy-guide-compact.md | docstring 改成 strategy-guide-compact.md，並註明 strategy-guide.md 是給人看的參考文件。 | 確認 |  |
| B160 | `backend/app/strategy/ai_generator.py:56` | 評分欄位白名單與執行期能力不一致：matrix 版拒收引擎支援的欄位（內建 consolidation_breakout 原始碼驗證失敗），卻接受台股沒有值的 total_shares／float_shares | matrix 白名單改由 backtest/matrix 模組匯出的支援判斷產生；polars 白名單排除 JOIN 欄與只在即時框架才有的欄位。補一個測試：所有內建策略原始碼都能通過 validate_code 的評分檢查。 | 確認 |  |
| B161 | `backend/app/strategy/ai_generator.py:124` | validate_code 判為有效的策略，引擎載入時會拒絕（執行後端與入口函式不符、asset_types、scoring_directions），而且不會觸發自動修復 | 讓 _strategy_entrypoint_error 依解析出的後端，套用與 engine._load_file 相同的入口規則；_validate_meta_semantics 改用 STRATEGY_ASSET_TYPES 與 meta_scoring_directions 檢查；把這幾類錯誤納入 needs_structural_repair。 | 確認 |  |
| B162 | `backend/app/strategy/ai_generator.py:140` | 桌面版 PyInstaller 規格沒有打包 strategy/prompts，打包版的 AI 策略指南與 step2 提示詞會靜默變成空字串 | 在 tickflow.spec 加上 datas += [(str(ROOT/'backend'/'app'/'strategy'/'prompts'), 'app/strategy/prompts')]；_load_doc 找不到檔案時記 warning 或直接報錯。 | 確認 |  |
| B163 | `backend/app/strategy/builtin/high_turnover_surge.py:91` | 高換手拉升的進場訊號標成 signal_volume_surge（放量、量比≥2），與實際條件不符 | 改用專屬 id（例如 signal_high_turnover_surge_entry），並在 field_labels.py 和前端 STRATEGY_SIGNAL_LABELS 加上標籤 | 確認 |  |
| B164 | `backend/app/strategy/custom_signals_ai.py:40` | 自訂訊號 AI 提示詞沒標示 turnover_rate 是百分數，同一段卻把 change_pct 標成小數；momentum_10d～60d、annual_vol_20d 也沒有單位 | 在 _format_fields 或 _SYSTEM_TEMPLATE 補上單位：turnover_rate 是百分數（5 表示 5%）；change_pct、momentum_*、amplitude、annual_vol_20d 是小數。並在 tests/test_custom_signals_ai.py 斷言提示詞含這些單位。 | 確認 |  |
| B165 | `backend/app/strategy/custom_signals_ai.py:52` | AI 策略與自訂訊號提示詞仍自稱「A股專家」，且沒有說明台股成交量以股計 | 人設改成台股；欄位說明標明 volume 單位為股（1 張 = 1000 股）、amount 單位為元、change_pct 為小數制。 | 可能成立 |  |
| B166 | `backend/app/strategy/custom_signals_ai.py:68` | 提示詞全文簡體並要求「中文」輸出，AI 產生的訊號名稱、策略名稱、RULES 與參數 label 會以簡體存檔 | 把提示詞改寫成繁體，並明確要求名稱、label、RULES 使用繁體中文；必要時在 parse_and_validate 之後做簡轉繁或檢查。 | 可能成立 |  |
| B167 | `backend/app/strategy/engine.py:469` | 策略檔名與標準模組同名時，會在整個行程取代該模組 | 模組名加前綴（例如 user_strategy_<stem>），或拒絕與已載入模組同名的檔名。 | 確認 | README「未排入階段的既有問題與限制（backlog）」第 3 點 |
| B168 | `backend/app/strategy/engine.py:488` | META 別名（STRATEGY_META、meta）通過存檔驗證，但引擎只讀 META，策略被載入成空 META | _prepare_strategy_code 只要 META 不是名為 META 的賦值就一律正規化；engine._load_file 改用 find_meta_assignment 的同一套名稱讀 META，或遇到別名時 fail-closed。 | 確認 |  |
| B169 | `backend/app/strategy/engine.py:501` | 策略來源以完整路徑子字串判斷，資料目錄含 /ai/ 或 /builtin/ 時會誤分類 | 建構 StrategyEngine 時傳入 {目錄: source} 對照，或依 path.parent.name 與已知策略根目錄的 resolve() 比對，不要用子字串。 | 確認 |  |
| B170 | `backend/app/strategy/field_labels.py:29` | field_labels.py 註解說與前端 cnSignal 一致，但「炸板」「跌停翹板」兩個標籤和前端不同，監控通知與規則畫面用語對不上 | 依 A05#7 的結論統一成一個符合定義的名稱，前後端同時修改，並加跨語言的一致性測試，或改由後端 API 提供標籤。 | 確認 | docs/audit-2026-09-24/sections/A05-limits-signals.md #7（相關：前端「跌停翻紅」標籤與定義不符） |
| B171 | `backend/app/strategy/monitor.py:1252` | 重啟後，持續成立的條件規則會立即重發告警，與策略、板塊規則的首輪靜默行為不一致 | 二選一並補測試：條件規則首輪只建立基準（與策略規則一致），或把 _condition_state、_last_fire 持久化，例如從 alerts.jsonl 回填當日最後觸發時間。 | 可能成立 | docs/code-review-2026-09-22.md B6 |
| B172 | `backend/app/strategy/monitor.py:1343` | 告警預設文案把小數制門檻與原始運算子直接印出（漲跌幅>=0.03），與同句的百分比不一致 | op_map 改用實際的 >=、<=、>、<、==、!=；change_pct、distance_to_* 這類小數制欄位乘 100 加 %，或改用前端 PERCENT_DECIMAL_FIELDS 的同一份清單。 | 確認 |  |
| B173 | `backend/app/strategy/monitor_rules.py:85` | strategy_monitor_ids 未驗證即用來組路徑，migrate/save_one 缺 ID_RE 檢查，具路徑穿越寫檔風險 | 在 set_realtime_monitor_config 對 strategy_monitor_ids 逐一套用策略 id 白名單（如 [A-Za-z0-9_-]+）；並在 save_one/migrate_strategy_monitors 寫檔前以 ID_RE 驗證 rule id，或 resolve 後確認落在 monitor_rules 目錄內（fail-closed）。 | 確認 |  |
| B174 | `backend/app/strategy/prompts/strategy-guide-compact.md:115` | AI 策略精簡指南沒寫 change_pct、momentum_*、amplitude 是小數制（完整版有寫），只有 turnover_rate 標了 % | 在精簡指南的價格與動量列補上「change_pct、momentum_*、amplitude 為小數（0.03 = 3%）」，並在 tests/test_ai_generator_prompt.py 加斷言。 | 確認 |  |
| B175 | `backend/app/strategy/prompts/strategy-guide-compact.md:130` | AI 策略精簡指南缺台股口徑：沒有代碼後綴與 boards，沒說三大法人與融資融券不在 enriched，不可用資料清單仍是 A 股項目 | 精簡指南補上：symbol 格式為 2330.TWSE／6488.TPEX；篩市場用 basic_filter.boards；三大法人、融資融券、借券不在 enriched 中。不可用清單改成台股項目。 | 可能成立 |  |

### 回測與挖掘（22 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B097 | `backend/app/backtest/engine.py:1037` | 7 處使用已棄用的 `collect(streaming=True)`，外層又是 `except Exception` 回傳空表；polars 升級拿掉這個參數後，回測面板與分鐘 K 查詢會靜默變成空資料 | 改成 `collect(engine="streaming")`，engine.py:80 改成 `is_in(symbols.implode())` 或 `symbols.to_list()`；pyproject 給 polars 加上主版本上限。 | 可能成立 |  |
| B098 | `backend/app/backtest/engine.py:1199` | 漲跌停只擋一字板：收盤鎖漲停仍可在收盤買進，開盤即漲停仍可在開盤買進，跌停賣出同理 | 依成交時點判斷：close_t 用收盤是否鎖在漲跌停價（limit flag），open_t+1 用開盤價是否等於漲跌停價（原始價與 ref_limit_*），一字板只是其中一種特例；補非一字鎖板的測試 | 可能成立 | docs/strategy-pick-tracking-plan.md:201 已記載回測「只擋一字漲停」、選股追蹤改擋「開盤即漲停」，但沒有列為缺陷 |
| B099 | `backend/app/backtest/engine.py:1328` | 全量模式加零股時每筆候選只買 1 股，最低手續費主導每筆報酬 | 全量模式以固定名目金額（例如 initial_capital/max_positions）換算股數，或在全量模式拒絕零股、忽略最低手續費，並在結果揭露樣本口徑 | 確認 |  |
| B100 | `backend/app/backtest/engine.py:1428` | 全量模式每筆候選都逐根掃描到矩陣尾端，形成 O(候選數×交易日) 的 Python 迴圈 | 迴圈前一次算出 present 矩陣（np.isfinite 的 OR），每檔預先取有效 bar 索引，逐根迭代到出場即停 | 確認 |  |
| B101 | `backend/app/backtest/engine.py:1461` | 全量（full）模式會丟掉到資料尾端仍無法出場的候選，造成存活者偏差 | 尾端仍 pending 的候選以最後收盤標記成未實現樣本並註記 exit_reason，或另外計入 execution.unclosed_at_end，並從 n_trades 的分母揭露；補測試 | 確認 |  |
| B102 | `backend/app/backtest/engine.py:1589` | 分鐘精確成交沒有檢查分鐘資料是否涵蓋整個交易時段，記錄器缺口也被當成完整一天 | 載入分鐘資料時檢查首根是否在 09:00～09:01、末根是否到 13:30，中間缺口是否超過門檻（可用 unattributed_volume）；不完整的日子一律降級並計入 execution 統計，POST 路徑也補上能力與覆蓋檢查 | 確認 | docs/audit-2026-09-24/sections/A11-backtest-costs.md:332-338 只記錄參考線含當日收盤（現已改為前一日 MA5），沒有記錄資料完整性 |
| B103 | `backend/app/backtest/engine.py:1856` | Polars 策略在 exit_fill=signal_next_minute 時，到期、期末出場的分鐘成交參考線用的是含當日收盤的 MA5 | signal_next_minute 的非訊號出場改用日線收盤（不做 refill），或參考線一律用前一日以前的資料；讓 polars 與矩陣兩條路徑一致 | 確認 | docs/audit-2026-09-24/sections/A11-backtest-costs.md:332（minute_fill 參考線含當日收盤；進場路徑已改用前一日 MA5，這條出場路徑仍存在） |
| B104 | `backend/app/backtest/engine.py:1915` | 組合模式被最少持有天數延後的訊號出場，交易紀錄的 exit_signal_id 遺失 | 最少持有分支先算出 signal_id 再傳給 _mark_pending，與 1918-1921 一致 | 確認 |  |
| B105 | `backend/app/backtest/fundamentals.py:163` | 財務因子的潛在問題：pb 用前復權價，矩陣路徑與 Polars 路徑的公告日解析與空值行為不同 | 矩陣路徑改用 snapshot['_announce']；空值的處理與 join_asof 一致（最新列為空值即為空值）；pb 改用 raw_close。若確定不再提供財務資料，可考慮整組下線（見 dead_code）。 | 可能成立 | docs/audit-2026-09-24/sections/G02-gap.md:76（財務因子在台股部署沒有資料） |
| B106 | `backend/app/backtest/matrix.py:1581` | 磁碟快取路徑與記憶體路徑的「可成交」判斷不同 | 抽出單一的可成交判斷函式，兩條建置路徑共用（可分塊寫入 memmap）。 | 可能成立 |  |
| B107 | `backend/app/backtest/matrix.py:2040` | 矩陣軸索引快取 .axes-*.json 永不清除，舊版本快取目錄只靠容量預算淘汰 | prune 時一併清理 .axes 檔（依 mtime 只保留最近幾份），並直接刪除 manifest 版本不等於 _DIRECT_MATRIX_LOADER_VERSION 的目錄。 | 確認 |  |
| B108 | `backend/app/backtest/matrix.py:2707` | 即時矩陣緩衝跨交易日沿用舊歷史：前一日保留盤中快照，除權息後歷史也不會重新復權 | 指紋加入歷史資料的最後交易日與 enriched generation，換日或資料重新發佈後就從 history 重建；至少在 append 一個新交易日時強制重新載入歷史。 | 可能成立 |  |
| B109 | `backend/app/backtest/matrix.py:4026` | 基本過濾的股價與市值上下限用前復權 close 比較 | 價格與市值過濾改用 raw_close（矩陣回測已載入 raw_close），Polars 路徑同步修改。 | 確認 |  |
| B110 | `backend/app/backtest/mining_runtime.py:1452` | 四因子候選名稱可能超過 80 字元，導致 promote 與 publish 一律被拒 | 在 _candidate_name 截斷到 80 字元，或驗證端改成截斷後再比較。 | 確認 |  |
| B111 | `backend/app/backtest/optimizer.py:63` | optimizer 用 min/max/step 展開時以 round 計算步數，會超過使用者給的 max，或把合法網格誤判為超出範圍 | 改成 n_steps = math.floor((hi - lo) / step + 1e-9)。 | 確認 |  |
| B112 | `backend/app/backtest/walkforward.py:126` | walk-forward 允許 step_days < test_days，OOS 窗互相重疊時複利報酬與一致性會重複計入同一段期間 | step_days < test_days 時直接拒絕；或聚合時只取不重疊的 OOS 區段來拼接。 | 確認 |  |
| B113 | `backend/app/backtest/worker.py:278` | 父行程被強制終止時，spawn 的 worker 子行程不會被回收，與新行程的 limiter 重疊執行 | 在子行程的 RSS 取樣執行緒裡順便檢查父行程是否還活著（psutil），父行程不在就 os._exit；或把子行程加入設定了 KILL_ON_JOB_CLOSE 的 Windows Job Object。 | 確認 |  |
| B114 | `backend/app/backtest/worker.py:310` | run_worker_task 在 get 逾時後才檢查 is_alive，子行程剛好在這段空檔送出結果並退出時，結果會被丟棄 | 偵測到子行程已結束後，再用 get_nowait 或短逾時 drain 一次佇列，確定沒有訊息才 break。 | 可能成立 |  |
| B145 | `backend/app/services/mining_candidates.py:424` | 候選晉升設定沿用 A 股成本預設（佣金 0.02%、印花稅 0.05%），與 mining 實際採用的台股成本不符 | 改成 request.get("commission_pct")（缺鍵時為 None）並固定 stamp_tax_pct=None，與 runtime 一致。 | 可能成立 | docs/audit-2026-09-24/README.md:549（A11-backtest-costs#0「挖掘另用 A 股印花稅 0.05%」，recheck 標已修正，但晉升 config 的預設值仍殘留） |
| B146 | `backend/app/services/mining_jobs.py:606` | Windows 上 manifest.json 的 os.replace 會撞上未加鎖或跨行程的讀取（WinError 5），成功的 mining 被標成 failed | 在 Windows 上對 os.replace 的 PermissionError 做短暫退避重試；list_runs 與 find_by_signature 的讀取也持鎖；artifact 註冊改由父行程依子行程回傳的結果執行，不要讓子行程寫 manifest。 | 確認 |  |
| B147 | `backend/app/services/mining_manager.py:238` | MiningJobManager 先寫終態、後寫終態事件，status 與 events 會短暫不一致（test_mining_manager 間歇失敗的根因） | 先 append_event 再 transition_status；或在 MiningRunStore 提供一個在同一段 _STORE_LOCK 內同時寫入狀態與事件的方法。 | 確認 | docs/audit-2026-09-24/README.md:321（測試衛生：test_mining_manager 既有競態，先寫狀態、後寫最後的事件） |
| B148 | `backend/app/services/mining_schedule.py:126` | 週度自動 mining 從未接線：run_weekly_mining 沒有任何生產呼叫端，排程設定開了也不會執行 | 在台股盤後更新流程的成功分支補回呼叫；或移除排程設定與 run_weekly_mining 相關程式，並修正文件。 | 確認 |  |

### API 層（21 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B076 | `backend/app/api/backtest.py:250` | /strategy/run 在 limiter 上無逾時、不可取消地等待，佔住 threadpool，前端放棄請求後仍會照跑 | 加上合理的 timeout 並回 503／429；或改成可取消的 job 模式（與 /strategy/stream 相同）。 | 確認 |  |
| B077 | `backend/app/api/backtest.py:415` | strategy/stream 在 generator 內建 config 失敗後，任務表留下永不完成的 job，同參數重連會永久空轉 | 把參數解析與 StrategyBacktestConfig 建構移到登記 job 之前，失敗時直接回 400；或在 generator 內 try/except，失敗時呼叫 _finish_job(job, error=...)。guard 與 minute_fill 的提早 return 也要 _finish_job，或在這兩種情況不登記 job。若確定不再使用，也可以整組移除（見 dead_code）。 | 確認 |  |
| B078 | `backend/app/api/financials.py:38` | 財務欄位讀 A 股的 /api/financials/metrics，台股部署永遠是空資料 |  | 確認 | docs/audit-2026-09-24/sections/G02-gap.md:25（recheck G02-gap#1 RC22，未修正） |
| B079 | `backend/app/api/financials.py:44` | /api/financials/metrics 在台股部署對任何代號都回 200 加空陣列 | 接上台股財報來源（例如 MOPS／TWSE 財報），或讓端點回明確的 unavailable 狀態（如 503 或 available=false），前端據此隱藏財務欄位組。 | 確認 | docs/audit-2026-09-24/sections/G02-gap.md G02-gap#1（README.md:1107、:680 RC22，仍未修正） |
| B080 | `backend/app/api/intraday.py:260` | /api/intraday/stream 每條 SSE 連線長期占用一個 asyncio 預設 executor 執行緒 | QuoteSubscriber 改用 asyncio 通知（loop.call_soon_threadsafe 設 asyncio.Event），或為 SSE 等待配置獨立且有上限的 executor，避免與 AI、mining 共用預設 executor。 | 可能成立 |  |
| B081 | `backend/app/api/kline.py:98` | /api/kline/instruments/search 忽略 asset_types，「大盤指數」監控規則搜尋不到指數，反而回傳股票與 ETF | 解析 asset_types：stock、etf 依 instrument_type 篩選；含 index 時把 BENCHMARK_INDICES 依代號或名稱比對的結果併入。補 asset_types=index 與 asset_types=stock 的測試。 | 確認 |  |
| B082 | `backend/app/api/kline.py:106` | /api/kline/instruments/search 收了 asset_types 參數卻不使用：前端按資產類型過濾的搜尋全部失效 | 在 search_instruments 解析 asset_types 並傳給 _search_taiwan_instruments 依 instrument_type 過濾；指數改從 benchmarks.BENCHMARK_INDICES 補上。或者在台股模式移除 RuleEditor 的「指數」資產類型，改導向板塊（sector_kind=index）監控 | 確認 |  |
| B083 | `backend/app/api/kline.py:581` | 多個端點的日期字串與 body 型別沒有驗證，格式錯誤時回 500 | query 參數改宣告為 date 型別，讓 FastAPI 回 422；body 改用 pydantic model（例如把現有的空殼 DailyBatchRequest 改成 BaseModel，days 用 Field(ge=5, le=60)，symbols 加 max_length）。 | 確認 |  |
| B084 | `backend/app/api/kline.py:609` | /api/kline/daily 的 live 後備路徑在台股 provider 下只抓當月且未前復權，並忽略請求的日期範圍 | 台股標的在 live 後備路徑傳入明確的 start_time/end_time（start 往前加 warmup），因子改用 _taiwan_local_factors 或 hybrid.get_adj_factors，結果依 start/end 過濾；或直接回 rows=[] 並附 reason，提示先完成歷史回補。 | 確認 |  |
| B085 | `backend/app/api/kline.py:804` | 日K即時蠟燭注入、預設 end 與板塊監控換日用伺服器本地日期 | 改用 app.market_time.cn_today() 或 taiwan.realtime.calendar.taipei_today()；sector_monitor 用 datetime.fromtimestamp(now, TAIPEI_TZ)。 | 可能成立 | docs/code-review-2026-09-22.md 三、次要問題第 3 條（kline.py date.today()） |
| B086 | `backend/app/api/kline.py:818` | A5 已知風險：池外標的取價在 API 執行緒同步進行，網路卡住時日K請求等待數十秒 | 對 fetch_live_bar 失敗的標的加短冷卻，或改為背景取價、下一次重抓時才注入。 | 確認 | docs/code-review-2026-09-22.md A5「已知風險（未處理）」第一段 |
| B087 | `backend/app/api/kline.py:984` | 平日國定假日 14:00 前，/api/kline/minute 與 minute-batch 不回退到上一交易日，分時圖空白 | need_fallback 加入 is_trading_day(today) is False（以及 preferences.get_taiwan_market_closed_date()==today）的判斷；兩處邏輯重複，可抽成共用函式並補平日假日測試。 | 確認 |  |
| B088 | `backend/app/api/kline.py:1278` | 台股預設資料源下 sync_minute_single 必定寫 0 筆卻回 200，多日分時圖反覆自動觸發 | 沒有任何可落庫的分鐘源時（非 custom 且 provider 為 taiwan），回 409 或 400 並說明台股分鐘K只來自盤中錄製，前端據此隱藏按鈕或停止自動補齊；_refresh_single_view 改在 executor 內、持 repo._lock 執行，並在 written=0 時略過。 | 確認 | docs/audit-2026-09-24/sections/G05-gap.md:304（已記錄 fetch_minute_single 在 taiwan provider 下回空表，但沒有涵蓋 sync_minute_single 的 rows=0 假成功與自動觸發） |
| B089 | `backend/app/api/screener.py:400` | get_cached 的 _attach_instrument_names 就地改寫與監控引擎共享的即時結果列 | 在 _cached_with_realtime 對併入的即時結果做深拷貝（至少複製 rows 與 row dict），或讓 _attach_instrument_names 回傳新列而非就地改寫，與 assign_grades 的複製不變式一致。 | 確認 |  |
| B090 | `backend/app/api/settings.py:113` | POST /api/settings/tickflow-key 匯入不存在的 is_invalid_key，端點每次都回 500 | 台股版已不使用 TickFlow key，建議刪除 POST/DELETE /tickflow-key 端點與前端的 saveTickflowKey/clearTickflowKey。若要保留，改用 base_tier_name() == "none" 判斷。 | 確認 |  |
| B091 | `backend/app/api/settings.py:930` | 偏好設定 API 啟用策略監控時讀 s.id 拋 AttributeError，錯誤被吞，規則不會遷移 | 改為 {s["id"]: s["name"] for s in ...}，並且不要靜默吞錯，至少記 warning。 | 確認 |  |
| B092 | `backend/app/api/settings.py:1162` | PUT /preferences/quote-interval 在沒有 quote_service 時對 None 呼叫 get_min_interval() | 降級分支比照 GET 版回傳常數 6.0，或改用 QuoteService 的 classmethod 或類別常數，不要透過 None 實例取值。 | 可能成立 |  |
| B093 | `backend/app/api/signals.py:148` | 自訂訊號編輯器與 AI 生成都沒有標示欄位單位，常數門檻容易差 100 倍或 1000 倍 | /options 為每個欄位回傳 unit（percent、decimal、shares、TWD）。UI 在常數輸入框旁顯示單位，百分比欄位提供 % 輸入並換算。AI 提示補上單位表，並改為台股語境。 | 可能成立 |  |
| B094 | `backend/app/api/stock_analysis.py:134` | /levels 回傳的 series、dates、summary 沒有前端消費者，ATR series 命名為 stop_loss／take_profit | 移除未使用欄位與元件，或接上畫面 | 確認 | sections/G04-gap.md G04-gap#9（recheck 判未修正） |
| B095 | `backend/app/api/strategy.py:479` | 儲存或重置策略參數覆寫後，沒有清除策略結果快取與監控選股池狀態 | 三個端點成功寫入後呼叫 _invalidate_strategy_runtime（或只失效該策略的快取條目與池狀態）。 | 確認 |  |
| B096 | `backend/app/api/strategy.py:790` | 策略 ID 衝突靠覆蓋檔案解決：/ai/save 會覆寫既有 AI 策略，create 模式會覆寫同名但載入失敗的檔案 | create 與 legacy 路徑在 path.exists() 或 load_errors 含同名檔時回 409；legacy /ai/save 改走 create 模式的衝突檢查，或直接移除。 | 確認 |  |

### 測試（19 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B223 | `backend/pyproject.toml:79` | integration 標記沒有註冊、也沒有預設排除，12 個真實連網的 smoke test 每次全套都會跑 | 在 pyproject 註冊 `markers = ["integration: ..."]`，並加上 `addopts = "-m 'not integration'"`；需要時再用 `-m integration` 單獨跑。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408（live_smoke 的基線失敗） |
| B224 | `backend/tests/conftest.py:17` | integration 標記未註冊也未排除，預設 pytest 就會打真實交易所與第三方 API；另有多個未標記的測試同樣連網 | 在 pyproject 註冊 integration 標記，並加上 addopts = "-m 'not integration'"。TestGolden2330Backtest 與上列 detail、comparison、ai_research 測試，要嘛補標 integration，要嘛把 hybrid_provider、index 服務、institutional／margin provider 換成假物件。 | 確認 | docs/audit-2026-09-24/README.md:302；docs/audit-2026-09-24/recheck-2026-10-06.md:376、408（已記錄連網與基線失敗，現況仍存在） |
| B225 | `backend/tests/conftest.py:27` | conftest 沒有隔離證券主檔全域單例：DATA_DIR 缺主檔時約 250 個測試會連網下載並寫入資料目錄，有主檔時則讀正式主檔 | 在 conftest 加一個 autouse fixture，從 tests/fixtures 載入一份小型主檔並 monkeypatch universe._default_master，同時讓 refresh 直接 raise。需要正式主檔的測試改標 integration。 | 確認 | docs/strategy-pick-tracking-plan.md:34；docs/audit-2026-09-24/README.md:302 |
| B226 | `backend/tests/conftest.py:56` | deny_network fixture 只替換 socket.connect，DNS 查詢仍會送出 | 同時 monkeypatch socket.getaddrinfo 與 socket.create_connection 拒絕非本機主機；或改用 sys.addaudithook 攔 socket.connect 與 socket.getaddrinfo 事件。 | 確認 |  |
| B227 | `backend/tests/test_ai_provider.py:555` | test_codex_exec_args_exclude_ephemeral 把清理函式換成空函式，每次執行都在正式 data/tmp 留下 tickflow-codex-run-* 目錄 | monkeypatch app.config.settings.cache_dir 為 tmp_path，或不要替換 _remove_tree_best_effort（它本來就是 best-effort）。 | 確認 |  |
| B228 | `backend/tests/test_auth_env_bootstrap.py:43` | test_auth_env_bootstrap 在設了 DATA_DIR 環境變數時，會把測試密碼寫進該 DATA_DIR 的真實 auth.json，並覆蓋既有密碼 | 測試裡先 `monkeypatch.delenv("DATA_DIR", raising=False)` 再建 Settings，或直接用 `Settings(_env_file=env_path, data_dir=tmp_path)`；並讓 is_configured 走真實邏輯（標 real_auth），或明確斷言寫入路徑在 tmp_path 底下。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408、:412（列為基線失敗 test_auth_env_bootstrap（1），沒有原因分析）；docs/audit-2026-09-24/recheck-2026-10-06.md:408（列為基線失敗 test_auth_env_bootstrap（1），未寫原因） |
| B229 | `backend/tests/test_mining_manager.py:24` | test_mining_manager 的等待函式只看狀態，不等最後一筆事件，三個測試會間歇失敗 | 讓 _wait_for_status 同時等到 read_events 的最後一筆事件型別等於目標狀態，或在 manager 內把狀態與事件放在同一次原子寫入。 | 確認 | docs/audit-2026-09-24/README.md:321（「test_mining_manager 有既有競態」，backlog） |
| B230 | `backend/tests/test_mining_manager.py:31` | test_mining_manager 的競態：測試先看到 manifest 已是終態，終態事件卻還沒寫入就去斷言事件序列 | 測試端：`_wait_for_status` 改成同時等終態事件出現（輪詢 read_events 的最後一筆）。產品端（可選）：改成先 append_event 再 transition_status，或讓兩者在同一個 store 鎖內完成。 | 確認 | docs/audit-2026-09-24/README.md:321（已記錄「先寫狀態、後寫最後的事件」） |
| B231 | `backend/tests/test_quote_index_merge.py:18` | test_quote_index_merge 兩個測試自 live write gate 上線後固定失敗（未傳 force、也沒有官方日資料） | 改成 force=True，或先寫入一筆官方日 K，讓 date_has_official_stock_data 為真；並改用台股指數代號。若 A 股指數 merge 路徑已不需要，就直接刪除這個檔。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408（列為基線失敗 test_quote_index_merge（2），未寫原因） |
| B232 | `backend/tests/test_quote_index_merge.py:38` | test_quote_index_merge 兩個測試是非密封測試：寫盤閘門讀的是全域 settings.data_dir，與測試的 tmp_path 不一致 | 測試裡呼叫 merge 時傳 `force=True`，或在 tmp_path 建 taiwan/daily/date=2026-07-28 並 monkeypatch settings.data_dir；也可以考慮讓 `_official_stock_dates` 改用 `TaiwanDailyStore(self.store.data_dir / 'taiwan' / 'daily')`。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408、:412（基線失敗 test_quote_index_merge（2），沒有原因分析） |
| B233 | `backend/tests/test_taiwan_ai_research.py:46` | AI 研究兩個測試直接讀正式資料目錄的 2026-08-28 行情，換一個 DATA_DIR 就失敗 | 比照同檔其他測試，注入 MagicMock 的 research_svc，或用 tmp_path 建立最小日 K、產業 fixture；不然就標成 integration。 | 確認 | docs/audit-2026-09-24/README.md:302（「各有 1 個案例……需要真實資料」） |
| B234 | `backend/tests/test_taiwan_ai_research_validation.py:361` | 兩個 AI 研究測試直接讀真實 data_dir 的 2330.TWSE 2026-08-28 資料，在隔離資料目錄下必定失敗 | 比照 test_taiwan_research_context 的做法，注入 MagicMock 的 daily／institutional／margin store 與 security_master，或在 tmp_path 建最小 fixture 分區並 monkeypatch settings.data_dir。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408（基線失敗 test_taiwan_ai_research（1）、test_taiwan_ai_research_validation（1），沒有原因分析） |
| B235 | `backend/tests/test_taiwan_backtest.py:89` | 既有測試仍會嘗試連外，TestGolden2330Backtest 需要真實資料 | 把真實資料的測試加上 marker 預設跳過，並在 conftest 預設啟用 deny_network。 | 確認 | README「第 2 階段的已知缺口」最後一點（留給第 6 階段的測試整理，仍未處理） |
| B236 | `backend/tests/test_taiwan_chip_history.py:129` | test_taiwan_chip_history 有日期腐化：寫死的日期配上未固定的 taipei_today()，從 2026-09-23 起固定失敗，12 月初還會再壞 7 個 | 在測試中 monkeypatch app.taiwan.chip_history.taipei_today 為固定日期（例如 2026-09-30），或讓測試日期依今天往回推算。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408（列為基線失敗 test_taiwan_chip_history（1），未寫原因） |
| B237 | `backend/tests/test_taiwan_chip_history.py:139` | test_non_contiguous_coverage_is_missing_dates_not_error 依賴實際的今天，2026-10 起必定失敗 | 測試中 monkeypatch app.taiwan.chip_history.taipei_today 固定日期，或用相對今天的日期產生資料。 | 確認 |  |
| B238 | `backend/tests/test_taiwan_corporate_actions.py:882` | test_taiwan_corporate_actions 的「更新按鈕」測試取決於正式主檔的上市日，會打真實 TWSE 18 次，離線時失敗 | 在此測試 monkeypatch app.taiwan.bootstrap.pending_listing_dates 回傳 []，或改用不含窗口內上市股的假主檔。 | 確認 | docs/audit-2026-09-24/recheck-2026-10-06.md:408（列為基線失敗「設定頁更新按鈕案例（1）」，未寫原因） |
| B239 | `backend/tests/test_taiwan_enrichment_live_smoke.py:50` | live smoke 測試把 AssertionError 轉成 skip，或把斷言放在條件裡，斷言可能永遠不會失敗 | 只在取資料的呼叫外包 try／except，捕捉網路例外（urllib.error.URLError、httpx.HTTPError、OSError），斷言放在 try 之外；空結果用 pytest.skip 明示，不要默默通過。 | 確認 |  |
| B240 | `backend/tests/test_taiwan_stock_detail_service.py:337` | test_service_index_limit_rule_is_not_applicable 透過全域指數服務連 TWSE，並把 TAIEX 日 K 寫進正式 data/taiwan/index_daily | 在 _offline_service 中 monkeypatch app.taiwan.detail_service.get_index_daily_service，讓它回傳一個 read_range 傳回空 DataFrame 的假物件；或在 conftest 統一替換 index_daily._service。 | 確認 | docs/audit-2026-09-24/README.md:302（只記錄此檔會連外，沒有記錄會寫入 index_daily） |
| B241 | `backend/tests/test_update_latest_chip_data.py:265` | 新測試會以預設資料根建立 store 並 import app.main，沒設 DATA_DIR 時會碰到正式 data 目錄 | 這個測試改用 monkeypatch 把 settings.data_dir（或 taiwan_data_root）指到 tmp_path；長期在 conftest 加 autouse fixture 隔離 data_dir。 | 確認 | docs/strategy-pick-tracking-plan.md:34（conftest 沒有隔離 data_dir）；docs/audit-2026-09-24 的 N2（import app.main 寫正式 backend.log，見專案記憶 flaky-and-env-dependent-tests） |

### 文件（18 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B070 | `.env.example:3` | TICKFLOW_API_KEY 仍被文件列為「選配資料來源」，但 TickFlow 已無法被選用 | 從 .env.example、configuration.md、deployment.md 移除 TickFlow 段落，或明確標示已停用。 | 確認 |  |
| B071 | `CONTRIBUTING.md:69` | CONTRIBUTING 模組邊界表對 backend/app/tickflow/ 與 backend/app/plugins/ 的職責描述已過時 | 更新表格：倉庫在 backend/app/repository.py，能力檢測在 backend/app/capabilities.py，插件載入在 data_providers/custom/loader.py，plugins/ 只是可選的投放目錄。 | 確認 |  |
| B072 | `README.md:40` | README／操作說明書宣稱只有五個主要頁面，漏掉側欄上的「策略庫」「策略回測」 | README 與操作說明書補上策略庫、策略回測兩節（可從 docs/features.md:5-33 搬過來），features.md:61 改寫清楚哪些功能不提供。 | 確認 |  |
| B073 | `README.md:63` | 文件說 AI 只用在兩個地方，漏掉個股頁的 AI 研究報告 | 三處都改成三個用途，並在操作說明書第 9 節補上個股 AI 研究報告的說明。 | 確認 | docs/market-filter-volume-patterns-plan.md:171 O2-8（已記錄「AI 只用在兩個地方」與實際不符，至今未修） |
| B074 | `README.md:136` | 文件說交易日 16:30 會更新資券，實際資券在 21:40 才排程抓取 | 改寫成：16:30 更新日線與法人，21:40 更新資券並重抓暫定日線；UI 的排程說明也同步改。 | 確認 |  |
| B075 | `README.md:164` | README／CONTRIBUTING 技術棧列了 lightweight-charts，但前端沒有任何地方使用 | 從技術棧移除，並刪掉這個依賴（見 dead_code）。 | 確認 |  |
| B244 | `docs/audit-2026-09-24/README.md:317` | 稽核 README 第 317 行說 features.md 還沒寫入變更日，但 features.md 已有，同頁第 194 行也說已寫入 | 刪掉 :317 後半句，只保留「績效頁顯示變更日」這項待辦。 | 確認 |  |
| B245 | `docs/configuration.md:36` | configuration.md 記載的 AI_DAILY_TOKEN_BUDGET 不存在，設了也不會限制 AI 用量 | 從文件刪掉；如果真的需要預算上限，另外實作。 | 確認 |  |
| B246 | `docs/configuration.md:104` | configuration.md 說環境變數優先級最高，實際上面板儲存的 AI 設定與 Key 會蓋過環境變數 | 改寫成：面板設定（data/user_data/secrets.json）> 環境變數 > .env，並說明要清掉面板設定才會改用環境變數。 | 確認 |  |
| B247 | `docs/custom-data-source.md:102` | custom-data-source 文件與範例使用 A 股代碼格式（000001.SZ），與台股標準代碼不符 | 範例全部改成 2330.TWSE、6488.TPEX 等台股代碼，並在欄位契約寫明代碼格式與成交量單位（股）。 | 確認 |  |
| B248 | `docs/deploy-password.md:18` | deploy-password.md 說 .env 可以放在 backend/，但後端不會讀 backend/.env | 改成「專案根目錄的 .env」。 | 確認 |  |
| B249 | `docs/deployment.md:14` | deployment.md 的 clone 指令指向原 A 股上游 repo，並要求填 TICKFLOW_API_KEY | 改成本 repo 的 URL 與目錄名，並刪掉 TICKFLOW_API_KEY 的說明。 | 確認 |  |
| B250 | `docs/deployment.md:56` | docs/deployment.md 引用不存在的 Docker build-arg INCLUDE_STOCKSDK，並指示 clone 上游 A 股專案 | 刪除 stock-sdk 段落，或改為實際存在的安裝方式；clone 網址改為本專案。 | 確認 |  |
| B251 | `docs/plugin-development.md:114` | plugin-development 說資料集未宣告時「自動回退 TickFlow」，但 TickFlow client 已改成一律拋錯 | 文件改寫成未宣告的資料集會改用台灣官方來源，或標明不支援；並同步修正 kline_sync 的回退邏輯（見另一條 correctness finding）。 | 確認 |  |
| B252 | `docs/plugin-development.md:121` | plugin-development／deployment 描述不存在的 stock-sdk、fuyao 插件與 INCLUDE_STOCKSDK 建置參數 | 刪掉 stock-sdk、fuyao 與 INCLUDE_STOCKSDK 相關段落，只保留插件清單格式與 loader 契約；deployment.md:49-60 也一併刪除。 | 確認 |  |
| B253 | `docs/taiwan-market-contract-v1.md:62` | contract v1 第 8 節仍說 TradingCostModel 的 docstring 稱 0.1425% 為官方上限 | 刪掉 docstring 那半句，保留「預設仍是 0.1425%」的違規描述。 | 確認 |  |
| B254 | `docs/taiwan-market-overview.md:58` | taiwan-market-overview 說台股資料在 backend/data/taiwan、必須從 backend/ 執行，與現行路徑解析不符 | 改成 `<DATA_DIR>/taiwan/`（預設是專案根目錄的 data/taiwan/），並刪掉必須從 backend/ 執行才能找到資料的說法。 | 確認 |  |
| B337 | `操作說明書.md:289` | 操作說明書／README 把 A 股的「排除 ST」寫成台股自選的風險警示過濾 | 文件刪掉 ST 描述；UI 移除這個按鈕，或改成台股實際的處置股、全額交割股標記。 | 確認 |  |

### 核心、資料源與排程（11 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B115 | `backend/app/data_providers/custom/loader.py:48` | 重新載入自訂資料源時會關閉仍在使用的 provider | 先建好新的 provider 字典再原子替換，舊 client 延後關閉。 | 可能成立 |  |
| B116 | `backend/app/data_providers/custom/provider.py:193` | 文件宣稱支援的自訂財務資料集從未被呼叫 | 補上同步流程，或從文件與驗證中移除 financial 資料集。 | 確認 |  |
| B117 | `backend/app/desktop.py:323` | 桌面版後端啟動失敗時靜默等滿 60 秒，關窗也不會優雅停止 | 輪詢時同時檢查 ready.is_set()，提早失敗並顯示 crash 對話框；關窗時設定 server.should_exit 並 join 執行緒。 | 確認 |  |
| B133 | `backend/app/jobs/daily_pipeline.py:244` | 排程盤後更新與策略重算完成後沒有發 SSE，開著的頁面不會刷新 | refresh_strategy_cache 成功後經 app_state.quote_service 呼叫 notify_strategy_results_updated；另外加一個 data_updated 事件，讓前端失效 taiwan-data-status、overview-market、kline 等查詢。 | 確認 |  |
| B134 | `backend/app/main.py:65` | N2 匯入 app.main 即把 log 檔掛到正式 data/backend.log，測試未隔離 | conftest 在 session 開始前把 DATA_DIR 設到 tmp，或把檔案 handler 移到 lifespan／uvicorn 啟動時才掛上。 | 確認 | docs/code-review-2026-09-22.md N2；docs/code-review-2026-09-22.md:663-666（N2，未修）；docs/market-filter-volume-patterns-plan.md:231 |
| B135 | `backend/app/main.py:326` | 關閉或重載時排程中的盤後更新不會停，進程退出會被它卡住 | 給排程 job 一個關閉用的 threading.Event，在 run_update 各階段之間檢查；或改用 daemon 執行緒的專用 executor，並在 shutdown 時記錄未完成的 job。 | 確認 |  |
| B136 | `backend/app/main.py:480` | SPA fallback 吞掉 /api 404 與 dist 根目錄靜態檔 | fallback 遇到 api/ 開頭的路徑回 404；dist 根目錄存在的檔案直接用 FileResponse 回傳。 | 確認 |  |
| B137 | `backend/app/repository.py:274` | 舊桌面版資料遷移把同名子目錄巢狀搬入，而不是合併 | 移除這段 A 股遺留遷移，或改成逐檔合併並在衝突時 fail-closed。 | 可能成立 |  |
| B138 | `backend/app/repository.py:290` | DuckDB 視圖 DDL 以字串拼接資料路徑 | 路徑中的單引號要跳脫（' → ''），或改用 DuckDB 參數化的表函式。 | 確認 |  |
| B139 | `backend/app/repository.py:578` | 啟動預熱執行緒與同步 refresh_cache 可同時執行，較慢的舊快照會覆蓋剛更新的快取 | _refresh_enriched 用一把鎖串行化，並以單調遞增的 refresh 序號或磁碟最新日期判斷，避免舊結果覆蓋新結果；同步刷新遇到預熱中時改成等它完成後再刷新一次。 | 可能成立 |  |
| B140 | `backend/app/secrets_store.py:40` | secrets.json 非原子寫入，損毀後再儲存會清空其他秘密 | 改成暫存檔＋os.replace 原子寫入，並加程序內鎖；讀到損毀檔時拒絕覆寫並保留備份。 | 確認 |  |

### 後端服務（11 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B141 | `backend/app/services/ai_provider.py:182` | _check_input_budget 錯誤訊息在 max_tokens=None 時顯示『輸出預算 None』 | 訊息改用 output_reserve 取代 max_tokens 做輸出預算顯示。 | 確認 |  |
| B142 | `backend/app/services/ai_provider.py:246` | ai_provider base_url 正規化對 Gemini/非尾端版本段仍補 /v1，造成 404 | 若 path 尾段含字母（如 openai）或已含任何 /vN 段，就不要再補 /v1；或改為僅在整個 path 為空/根時補 /v1。 | 確認 |  |
| B143 | `backend/app/services/data_integrity.py:128` | 完整性報告在今天的分區已存在時，漏報前一交易日的 provisional 狀態 | 改成只豁免 stored[-1]：provisional_dates([d for d in present if d != stored[-1]])，並補一個 today 分區存在的測試。 | 確認 |  |
| B144 | `backend/app/services/kline_sync.py:858` | 自訂分鐘源失敗後的「回退 TickFlow」路徑必定拋 RuntimeError，導致分時端點回 500、整輪監控被丟棄 | 移除已不存在的 TickFlow 回退：自訂源失敗時記 warning 並回傳空表，交給呼叫端走本地資料。 | 確認 |  |
| B149 | `backend/app/services/notify_adapter.py:129` | 系統通知在行情輪詢執行緒上同步發送，macOS/Linux 每筆最多阻塞 5 秒 | 比照 webhook，將系統通知投遞改到獨立執行緒池 fire-and-forget，或限制每輪通知數並縮短 timeout。 | 可能成立 |  |
| B150 | `backend/app/services/pipeline_jobs.py:181` | 設定頁「逾時設定」可以儲存，但沒有任何任務會讀取 | 移除這張卡片與對應的偏好設定，或接到實際的長任務上。 | 確認 |  |
| B154 | `backend/app/services/regime_builder.py:637` | regime 計算沒有接上管線，指數仍用 A 股代號 | 刪除 regime 管線與開關，或改用 TAIEX 並接到 daily_pipeline。 | 確認 | recheck-2026-10-06.md A12-screener-stats#8（RC22）；sections/A12-screener-stats.md A12-screener-stats#8（recheck 判未修正） |
| B155 | `backend/app/services/strategy_pick_history.py:147` | 複合策略的指紋與輸入分組只看 META 宣告的子策略，忽略引擎實際使用的已存子策略覆寫 | 兩個函式都改用 engine.composite_children(strategy, all_overrides.get(sid))（輸入分組需要另外傳入 overrides），與執行端及 pick_grades 一致 | 可能成立 |  |
| B156 | `backend/app/services/strategy_pick_history.py:429` | 補算寫入在 part.parquet 與 runs.parquet 之間中斷時，該策略日永久變成 manifest_missing，且沒有修復路徑 | reconstructed 寫入時，若既有 manifest 無效且舊列的 provenance 不是 recorded（或沒有清單列），允許覆寫；或者先寫 runs 暫存，再一次換名兩個檔案 | 可能成立 |  |
| B157 | `backend/app/services/strategy_pick_performance.py:301` | 上櫃轉上市後代號尾碼改變，舊代號被判為缺席，報酬凍結在轉板前的收盤價 | 建立代號轉板對照（證券主檔的 listing／delisted 資訊，或以「同一數字代號在另一交易所接續出現」判斷），計算出場價與 absent_since 時把新舊尾碼視為同一標的；至少不要標成可能下市 | 確認 |  |
| B158 | `backend/app/services/strategy_pick_performance.py:523` | absent_count 包含未成交的選股，畫面卻說「已計入統計，報酬沿用最近一筆收盤價」 | absent_count 改以 filled（status 為 tracking 或 completed）計算，或另外拆出「未成交且缺席」的計數；前端提示依計數口徑調整 | 確認 |  |

### 建置與部署（8 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B222 | `backend/pyproject.toml:57` | 開發工具宣告成 optional extra `dev`，uv sync 的 exact sync 不會安裝、甚至會移除 pytest/ruff/mypy | 把 dev 改為 `[dependency-groups] dev = [...]`（uv sync/uv run 預設會安裝，--no-dev 才會生效）；啟動器的 uv sync 加 --inexact，或改用 --all-groups。 | 確認 |  |
| B242 | `dev.ps1:179` | dev.ps1 是無 BOM 的 UTF-8，Windows PowerShell 5.1 以 ANSI 解碼導致中文橫幅亂碼 | 把 dev.ps1 存成 UTF-8 with BOM，或把字串改為 ASCII。 | 確認 |  |
| B243 | `docker-compose.yml:32` | .env.example 缺少 compose 與 Settings 會讀取的鍵（CODEX_HOME_HOST 等），Windows 下 Codex 掛載路徑失效 | 在 .env.example 補上 CODEX_HOME_HOST（附 Windows 範例 %USERPROFILE%/.codex）與其餘可調鍵（可註解掉並附預設值）。 | 可能成立 |  |
| B332 | `packaging/tickflow.iss:29` | tickflow.iss 本機建置時版本是 0.0.0，註解卻說版本讀自 package.json | 用 ISPP 讀 VERSION：`#define MyAppVersion Copy(Trim(FileRead(FileOpen("..\VERSION"))), 2)`，並修正註解。 | 確認 |  |
| B333 | `packaging/tickflow.iss:144` | tickflow.iss 的 InitializeWizard 無條件改寫安裝目錄，可能蓋掉升級時的舊安裝路徑 | 改成 `if (WizardForm.PrevAppDir = '') and not DirExists('D:\') then ...`，或用 `DefaultDirName={code:GetDefaultDir}`。 | 可能成立 |  |
| B334 | `packaging/tickflow.spec:136` | app.custom 擴展模組不會被 PyInstaller 收集，桌面版靜默失去二開擴展 | spec 加 `hiddenimports += collect_submodules('app.custom')`，或在文件註明桌面版不支援源碼擴展。 | 確認 |  |
| B335 | `scripts/upgrade_check.py:54` | upgrade_check.py 的 merge-tree 解析把單邊新增檔中的 `=======` 行誤判為衝突 | 改用 `git merge-tree --write-tree --name-only --messages <current> <target>`（Git ≥2.38）取得真正的衝突清單，或只在 both 區段內判斷標記，且要求標記行完全等於 `+=======`。 | 確認 |  |
| B336 | `scripts/upgrade_check.py:94` | scripts/upgrade_check.py 在 zh-TW Windows 以管線或重新導向執行時，印出簡體字崩潰 | 在 main 開頭執行 sys.stdout.reconfigure(encoding='utf-8', errors='replace')，並把輸出文字改成繁中；或只輸出 ASCII。 | 確認 |  |

### 即時行情（6 項）

| ID | 位置 | 問題 | 建議修正 | 驗證 | 既有文件 |
|---|---|---|---|---|---|
| B151 | `backend/app/services/quote_service.py:314` | stop() 的 join(10) 逾時之後再 enable()，會同時跑兩條輪詢執行緒 | 每次啟動建立新的 threading.Event 或世代編號，迴圈只認自己的那一個；enable() 在舊執行緒仍存活時先等它結束，或者拒絕重啟。 | 可能成立 | 未見於既有審查文件 |
| B152 | `backend/app/services/quote_service.py:331` | 即時行情開關的 start/enable/stop 沒有鎖，可能同時跑兩條輪詢執行緒 | 用鎖保護 start/enable/stop；每條輪詢執行緒帶自己的 stop Event 或世代編號，迴圈只看自己的；stop 改成不阻塞 API（只設 Event）。 | 可能成立 |  |
| B153 | `backend/app/services/quote_service.py:782` | 台股輪詢池從不請求 CORE_INDEX_SYMBOLS，每輪都用空表覆寫指數即時快取 | 台股分支把 CORE_INDEX_SYMBOLS（或 preferences 的指數清單）併入請求池；沒有指數報價時保留上一份快取，不要覆寫成空表。 | 確認 | docs/code-review-2026-09-22.md B4；docs/code-review-2026-09-22.md B4「台股路徑從不請求指數，卻每輪用空結果覆寫指數快取 — 待修」（現行程式碼仍存在） |
| B209 | `backend/app/taiwan/realtime/mis_provider.py:155` | MIS 回應的 ch 沒有 tse_/otc_ 前綴，主對照永遠落空；備援只比對代號，同代號跨市場時會錯配 | 用 item['ex'] 加 item['c']（或 item['key'] 的前段）組出 '{ex}_{c}.tw' 再對照；找不到時才退回只比代號，而且只在請求中該代號唯一時採用。 | 可能成立 | 未見於既有審查文件 |
| B210 | `backend/app/taiwan/realtime/service.py:139` | 輪詢時 MIS 缺價的標的逐檔同步呼叫 Yahoo，結果卻全部被 hybrid 丟棄 | 讓 get_quotes 接受「不要第三方備援」的參數，hybrid 輪詢路徑關掉 Yahoo；或在 Yahoo 端加短期負向快取與總時間上限，並改成並行或批次請求。 | 確認 | docs/code-review-2026-09-22.md A1「副作用（已知，待觀察）：盤前試撮時段……整批會落到 Yahoo，輪詢成本上升……影響有界」。該文件沒有指出這些 Yahoo 結果在輪詢路徑上全部被丟棄。 |
| B211 | `backend/app/taiwan/realtime/service.py:239` | TaiwanRealtimeService 會把快取命中的舊報價重新蓋上新 TTL，報價可能長時間不重抓 | 第 5 步只寫回本輪真正抓到的標的（to_fetch 的結果），不要重寫快取命中項；並為缺價標的加短 TTL 的負向快取。 | 確認 | docs/audit-2026-09-24/sections/A13-realtime.md「報價快取 TTL 3 秒、輪詢 6 秒」只驗證了單純輪詢的情境，沒有涵蓋這個重新蓋 TTL 的路徑。 |

