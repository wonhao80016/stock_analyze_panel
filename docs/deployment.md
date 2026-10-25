# 部署指南

本專案的幾種執行方式，依推薦程度排序。設定項詳解見 [configuration.md](./configuration.md)；桌面安裝包的下載與安裝見[操作說明書第 2 節](../操作說明書.md#2-安裝與啟動)。

> 📌 前置依賴：Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`（`npm i -g pnpm`）

---

## 方式 A：開發模式（二次開發推薦）

開發模式可以隨時 `git pull` 同步最新程式碼。

```bash
git clone https://github.com/Itoy1212/TWstockfor_tick-stock-panel.git
cd TWstockfor_tick-stock-panel
cp .env.example .env       # 日常使用不需要填任何 API Key
./dev.sh                   # Windows: .\dev.ps1
```

`dev.sh` 會自動檢查並下載相依套件、釋放連接埠、同時啟動前後端，Ctrl-C 一併關閉。預設：

- 後端 → <http://localhost:3018> · 前端 → <http://localhost:3011>
- 自訂連接埠：`BACKEND_PORT=8000 FRONTEND_PORT=5173 ./dev.sh`

### 手動分別啟動（不想用 dev.sh）

```bash
# 後端
cd backend && uv sync
# 老 CPU: uv sync --extra legacy-cpu
uv run uvicorn app.main:app --reload --port 3018

# 前端
cd frontend && pnpm install && pnpm dev   # http://localhost:3011
```

---

## 方式 B：Docker（部署最省心）

```bash
cp .env.example .env
docker compose up --build
# 開啟 http://localhost:3018
```

Docker 採用兩階段建置，前端 dist 複製進後端映像檔，**單容器**執行，資料都在主機掛載的 `data/` 目錄。

啟動前請先在 `.env` 設定 `AUTH_PASSWORD` 預設存取密碼：主機瀏覽器經 Docker 網路閘道連進容器，不算本機，未設密碼時面板會拒絕所有請求（見下方「訪問密碼設置」）。

Dockerfile 預設 `USE_CN_MIRROR=1`，透過中國大陸的 npm／PyPI 鏡像安裝相依套件。在其他地區建置較慢或失敗時，可以關掉鏡像直連官方來源：

```bash
docker compose build --build-arg USE_CN_MIRROR=0
docker compose up -d
```

更新到新版本：

```bash
git pull
docker compose up --build -d
```

---

## 老 CPU 兼容(avx2/fma 缺失)

如果執行時出現 `avx2`／`fma` 缺失，或行程 `exit 132`，代表 CPU 不支援 AVX2 指令集（常見於老 VPS）。解決方式：

- **開發模式**：在根目錄 `.env` 設定後執行 `./dev.sh` 或 Windows 的 `.\dev.ps1`；即使已有 `.venv`，啟動器也會同步相容核心
- **Docker**：在根目錄 `.env` 設定後執行 `docker compose up --build`

```ini
BACKEND_EXTRAS=legacy-cpu          # 相容老 CPU
```

手動啟動原始碼時，也可以在 `backend/` 目錄直接執行 `uv sync --extra legacy-cpu`。不要設定 `POLARS_SKIP_CPU_CHECK`，它只會隱藏警告，實際執行不支援的指令時仍可能崩潰。

### 回測依賴說明

回測引擎以 Polars／NumPy 實作，矩陣計算以 numba 加速（沒有 numba 時退回純 Python），不需要額外 extras。`backtest` extra 保留為空以相容舊設定，`uv sync --extra backtest` 與 `BACKEND_EXTRAS=legacy-cpu backtest` 仍可正常執行。

---

## 更新代碼(已部署用戶必讀)

拉取新版本只需一條指令：

```bash
git pull
```

**整個 `data/` 目錄都不納入 git**：行情日 K、三大法人、融資融券、基本面、自選清單與分組、監控規則與觸發記錄，全部是程式執行時產生或下載的使用者資料，`git pull` 不會動到它們。新使用者首次啟動後，請到面板 **設定 → 資料來源 → 台股歷史日 K 資料庫** 下載歷史資料包（GitHub Release `data-daily-2026-09-10`，範圍 2024-01-02 ～ 2026-09-10），之後的日常更新由排程自動處理。

> ⚠️ **切勿用以下指令「解決衝突」或「清理」，它們會一次刪光 `data/` 下所有未被 git 追蹤的資料：**
> - `git clean -fdx`（最危險，會刪掉所有 `.gitignore` 忽略的檔案）
> - `git reset --hard`
> - 直接刪除整個專案資料夾重新 `git clone`
>
> 若 `git pull` 回報衝突，通常是本機誤改了被追蹤的檔案，請先 `git stash` 暫存再 pull，不要直接執行上面的指令。

### 2026-10 策略與選股更新（第 4 階段）的升級須知

這次更新改了指標定義與多支策略的規則。已部署的環境升級前，請先讀完本節。

**升級前先備份**（先停掉後端，或確定備份完成前不會觸發台股資料更新）：

- `data/kline_daily_enriched/`（全部分區）
- `data/.taiwan_enriched_factors.json`、`data/.matrix_generation_stock.json`
- `data/user_data/strategy_cache.json`、`data/user_data/strategy_picks/`
- `data/taiwan/share_capital.parquet`

`data/.backtest_matrix_cache/` 可以重建，不必備份。

**升級後第一次資料更新會全量重寫 enriched**：發佈紀錄裡沒有新的指標定義版本（`INDICATOR_DEFINITION_VERSION` = 2），所以升級後第一次台股資料更新（盤後排程 16:30／21:40、設定頁「更新到最新」或匯入歷史資料包）會重寫全部 enriched 分區一次，已發佈的換手率會保留。2026-10-10 在 670 個分區的資料複本上實測約 73 秒。

- 重寫完成前，矩陣即時計算的指標（新慣例）與已發佈的指標（舊慣例）不一致。
- 完成後確認 `data/.taiwan_enriched_factors.json` 有 `"indicator_version": 2`，並等策略快取重算（盤後排程更新完會自動重算）。

**行為變更（其他安裝請特別注意）**：

- 評分缺值：有評分設定的策略，缺評分資料的候選分數為空、排在最後，標「觀察・資料不足」；0 分是正常分數。`/api/screener/cached` 的 `grade_reason` 由 `zero_score` 改為 `score_unavailable`，每列另有 `limit_up_locked`。
- 疊加策略：子策略的 META 寫了 `descending=False` 時，這個疊加策略不再載入。錯誤列在策略重新載入回應的 `load_errors`，其他策略照常載入；保存的子策略設定裡含這種策略時，執行會報錯。請先移除子策略 META 裡的 `descending=False`（載入檢查只看 META，改設定無法解除），再用 META 或設定裡的 `scoring_directions` 指定低值優先的欄位。
- 已保存的策略設定，`scoring` 若還有 `turnover_rate`（`bullish_alignment`、`low_volatility_leader`、`pullback_to_support` 在本次更新前的預設評分），會與新的預設評分合併：沒有換手率的日子分數都是空值，回測也會因覆蓋率不足被拒絕。請在策略設定裡移除這個評分欄位。
- `pullback_resume`、`swing_trend_confirmation` 的流動性門檻改為成交額 ≥ 1 億元，取代 200 萬股；已保存的 `min_volume` 仍會疊加套用，比新預設更嚴。
- `high_turnover_surge` 的預設門檻由 5% 改為 2%，已保存的 `min_turnover` 保留原值。換手率分母改為已發行普通股扣除私募股；沒有換手率資料的日期與回測區間會被拒絕。
- `market_cap_*`、`float_cap_*` 上下限：台股資料沒有股本欄位，這類上下限在選股與回測中都會被忽略。
- 新股排除 `exclude_new_days` 開始生效，以交易日計，預設 30。
- 訊號 ID 改名：進場訊號有 `bullish_alignment`、`low_volatility_leader`、`n_day_low_reversal`、`pullback_ma20_bounce`、`pullback_to_support`；出場訊號有 `bottom_reversal`（改為 `signal_ma20_breakdown` 與 `signal_close_20d_low`）與 `swing_trend_confirmation`（新增 `signal_swing_structure_exit`）。監控規則命中的訊號名稱跟著變，買進訊號的去重狀態可能讓同一檔再提醒一次。
- 已記錄的選股快照不回改。請記下升級後第一次重算策略的日期作為策略邏輯變更日，變更日前後的事後統計不可直接合併。

**已知限制**：

- 換手率：一天的換手率固定在它最後一次以最新交易日身分發佈時的值，事後修正成交量不會更新；其他情況見 [`taiwan-share-capital-phase-6h.md`](taiwan-share-capital-phase-6h.md) 的「已知限制」。
- 只有儲存欄、沒有發佈指標的 enriched 列（例如強制寫入的即時列）若是某檔在載入區間的第一列，回測補算的漲跌停旗標會是 False，因為缺前一日收盤。只在發生過強制即時寫入時出現。
- 有這類只有儲存欄的列時，回測與快取重建要為受影響的股票重算漲跌停旗標：實測 140 萬列多約 4.5 秒，Polars 回測從約 6 秒變成約 13 秒。
- 加權指數的長區間 K 線（例如 1 年）會在請求中同步回補本機缺少的較早月份，每個月份 20 秒逾時，失敗的月份 600 秒後才重試；網路不通時，最壞約每 10 分鐘卡住約 80 秒。
- `exclude_st`：台股選股時沒有名稱欄位，這個條件不生效；Polars 回測則用證券主檔的名稱比對，兩邊可能不一致（目前沒有台股名稱會命中）。
- 手動放進策略目錄、檔名與 Python 模組同名的策略檔（例如 `json.py`），會在整個後端行程中取代該模組；透過介面保存的策略不會。
- 自訂歷史窗口策略（`filter_history`）在回測時收到整段資料，只能用按股票的時序運算，寫法見 `backend/app/strategy/prompts/strategy-guide.md`。

---

## 訪問密碼設置(公網部署必讀)

未設存取密碼時，面板只接受**本機**（`127.0.0.1`／`::1`）的請求，區網與公網請求一律拒絕，以防他人搶先設定密碼鎖死你的面板，或在你設密碼前直接操作面板。

如果你從區網或公網的瀏覽器直接開啟頁面，後端會回覆以下訊息（原文）：

> 未设访问密码时仅允许本机(127.0.0.1 或 ::1)访问。请先在本机浏览器设置密码,或以 AUTH_PASSWORD 环境变量预设密码后重启服务

有兩種方式解決，任選其一。

### 方式一：環境變數預置密碼（最簡單，推薦）

在**專案根目錄的 `.env`**（與 `.env.example` 同一層；後端不會讀 `backend/.env`），或 Docker／系統環境變數裡設定 `AUTH_PASSWORD`：

```bash
AUTH_PASSWORD='你的密碼'
```

然後重新啟動服務。啟動時會自動：

1. 讀取 `AUTH_PASSWORD`
2. 用 PBKDF2 雜湊後寫入 `data/user_data/auth.json`（`chmod 600`，只存雜湊不存明文）
3. **之後不再讀取這個環境變數**：這是一次性的初始化

設定完成後即可用公網位址加這組密碼登入。之後要改密碼請在面板的「設定 → 帳戶安全」操作，不受環境變數影響。

**注意事項：**

- **密碼至少 6 個字元**，否則會被略過並記一筆 warning 日誌
- **只在尚未設過密碼時生效**。已設過密碼後，改這裡不會覆蓋（避免重啟時把你在面板改的密碼重設回去）
- 密碼建議用單引號包住，避免 Docker Compose 對 `$VAR` 插值；啟動時也會從唯讀掛載的原始 `.env` 初始化，相容既有未加引號的設定
- `.env` 檔案權限保持 `600`，**不要提交到 Git**
- 明文密碼只存在 `.env`／環境變數中，寫入磁碟的是雜湊，安全性等同 `auth.json`

### 重置密碼（忘記密碼時）

停止服務後**刪除** `data/user_data/auth.json`，再重新啟動，面板會回到「未設密碼」狀態，此時 `AUTH_PASSWORD` 會重新生效。只能刪除檔案，不能清空內容或改寫成 `{}`：檔案存在但內容無效時，後端會判定密碼檔損毀並拒絕存取。

```bash
rm data/user_data/auth.json   # 停止服務後執行，刪除後重新啟動
```

### 方式二：SSH 連接埠轉發

不用改設定，在你**自己電腦**的終端機執行（不是伺服器上）：

```bash
ssh -L 3018:127.0.0.1:3018 使用者名稱@伺服器IP
```

例如伺服器是 `123.45.67.89`、使用者名稱 `root`、面板連接埠 `3018`：

```bash
ssh -L 3018:127.0.0.1:3018 root@123.45.67.89
```

保持這個 SSH 連線**不要關閉**，然後在**自己電腦的瀏覽器**開啟 `http://127.0.0.1:3018`。此時後端看到的客戶端 IP 是 `127.0.0.1`（本機），能通過檢查，正常顯示設定密碼畫面。

**設完密碼後**，SSH 連線可以斷開：密碼已存在伺服器上，之後直接用公網位址加剛設的密碼存取即可。

> `3018` 是預設連接埠。如果用 `PORT` 改過連接埠（例如 `PORT=8080`），兩處都要替換：`ssh -L 8080:127.0.0.1:8080 root@IP`。

### 兩種方式怎麼選

| | 環境變數 | SSH 轉發 |
|---|---|---|
| 操作 | 改一行設定並重啟 | 一條 ssh 指令 |
| 需要改設定 | 是 | 否 |
| 適合 | Docker／自動化部署／不熟 SSH | 臨時設密碼／能 SSH 到伺服器 |
| 之後改密碼 | 面板「設定 → 帳戶安全」 | 同左 |

推薦**方式一（環境變數）**，設定一次即可。

> **Docker 部署必須用方式一。** 主機瀏覽器連進容器時，容器看到的來源是 Docker 網路的閘道位址（`172.x.x.x` 之類），不是 `127.0.0.1`；所以即使在同一台電腦開啟 `http://localhost:3018`，未設密碼時也會被拒絕，SSH 轉發到主機的 `127.0.0.1` 也一樣。請先在 `.env` 設好 `AUTH_PASSWORD` 再啟動容器。

### 原理說明

- **為什麼只限本機？** 能連到伺服器的人（包括同一區網的其他裝置）都能存取 URL。如果不限制，他人可以在你之前開啟頁面、設一組密碼把面板鎖死，或在你設密碼前直接操作面板。
- **本機如何判斷？** 客戶端 IP 必須是 `127.0.0.1` 或 `::1`，而且請求的 Host 必須是 `localhost`、`127.0.0.1` 或 `[::1]`（可帶連接埠）。Host 檢查用來擋 DNS rebinding：惡意網站把自己的網域解析到 `127.0.0.1` 時，瀏覽器送出的 Host 仍是該網域。
- **跨來源請求：** 面板只接受同源請求。瀏覽器送出的 `Origin` 與請求的 Host 不同時，`/api/` 一律回 403，其他網站無法借你的瀏覽器操作面板。
- **SSH 轉發為什麼有效？** `-L` 把本機連接埠透過 SSH 通道轉發到伺服器的 `127.0.0.1`，等同在伺服器本機存取，客戶端 IP 變成 `127.0.0.1`，通過檢查。
- **反向代理注意：** 後端不自行解析 `X-Forwarded-For`，客戶端 IP 由 uvicorn 決定：只有直連來源列在 `FORWARDED_ALLOW_IPS`（預設 `127.0.0.1`）時，才採用 `X-Forwarded-For` 最右側不受信任的位址。反代與面板不在同一台機器，或面板跑在 Docker 內時，要把反代連進來的位址設進 `FORWARDED_ALLOW_IPS`，否則登入限流會把所有人算成同一個來源；同時面板連接埠只能讓反代連到（例如 Docker 以 `HOST=127.0.0.1` 只綁本機），否則直連面板的人也會被當成反代，可以偽造 `X-Forwarded-For`。反代也要原樣轉發瀏覽器送來的 Host（含連接埠；Nginx 用 `proxy_set_header Host $http_host;`，不要用不帶連接埠的 `$host`），否則瀏覽器的 `Origin` 與 Host 不符，請求會被拒絕。
- **Docker 內的 `FORWARDED_ALLOW_IPS` 怎麼填：** 反代在主機上時，容器看到的來源是 compose 網路的閘道，不一定是 `docker0` 的 `172.17.0.1`。先 `docker compose up` 一次，再用 `docker network inspect <專案名>_default --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'` 查出閘道位址填入（專案名預設是 compose 檔所在目錄名，可用 `docker network ls` 確認）；反代也是同一個 compose 網路裡的容器時，改填反代容器在該網路內的位址。
