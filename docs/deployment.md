# 部署指南

本項目的幾種運行方式，按推薦程度排序。配置項詳解見 [configuration.md](./configuration.md)。

> 📌 前置依賴:Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`（`npm i -g pnpm`）

---

## 方式 A:Dev 模式(二次開發推薦)

由於剛開源近期更新頻繁,推薦開發模式運行,可隨時 `git pull` 同步最新代碼。

```bash
git clone https://github.com/shy3130/tick-stock-panel.git
cd tick-stock-panel
cp .env.example .env       # 按需填 TICKFLOW_API_KEY(留空 = None 模式)
./dev.sh                   # Windows: .\dev.ps1
```

`dev.sh` 自動檢查 / 下載依賴、釋放端口、同時起前後端,Ctrl-C 一併關閉。默認:

- 後端 → <http://localhost:3018> · 前端 → <http://localhost:3011>
- 自定義端口:`BACKEND_PORT=8000 FRONTEND_PORT=5173 ./dev.sh`

### 手動分別啟動(不想用 dev.sh)

```bash
# 後端
cd backend && uv sync
# 老 CPU: uv sync --extra legacy-cpu
uv run uvicorn app.main:app --reload --port 3018

# 前端
cd frontend && pnpm install && pnpm dev   # http://localhost:3011
```

---

## 方式 B:Docker(部署最省心)

```bash
cp .env.example .env
docker compose up --build
# 打開 http://localhost:3018
```

Docker 採用兩階段構建,前端 dist 拷進後端鏡像,**單容器**運行,數據完全在自己手裡。

> ⚠️ **stock-sdk 插件默認不打包(合規考慮)**
>
> stock-sdk 數據源本質是抓取第三方財經網站(如東方財富)的行情接口,未經對方授權,可能違反其服務條款並涉及交易所行情版權問題。**出於合規考慮,Docker 默認構建不再內置 stock-sdk 插件依賴**。
>
> - **默認行為**:`docker compose up --build` 構建出的鏡像**不含** stock-sdk,插件不可用。
> - **如確需啟用**(自行承擔合規責任):
>   ```bash
>   docker compose build --build-arg INCLUDE_STOCKSDK=1
>   docker compose up -d
>   ```
> - 啟用後鏡像會額外內置 Node.js 運行時並預裝 stock-sdk 依賴,插件開箱即用。
> - **建議優先使用正規授權數據源。**

更新到新版本:

```bash
git pull
docker compose up --build -d
```

---

## 老 CPU 兼容(avx2/fma 缺失)

如果運行時報 `avx2`/`fma` 缺失,或進程 `exit 132`,說明 CPU 不支持 AVX2 指令集(常見於老 VPS)。解決:

- **Dev 源碼啟動**:在根目錄 `.env` 設置後運行 `./dev.sh` 或 Windows 的 `.\dev.ps1`;即使已有 `.venv`,啟動器也會同步兼容內核
- **Docker**:在根目錄 `.env` 設置後執行 `docker compose up --build`

```ini
BACKEND_EXTRAS=legacy-cpu          # 兼容老 CPU
```

手動啟動源碼時，也可以在 `backend/` 目錄直接執行 `uv sync --extra legacy-cpu`。不要設置 `POLARS_SKIP_CPU_CHECK`，它只會隱藏警告，實際執行不支持的指令時仍可能崩潰。

### 回測依賴說明

回測引擎以 Polars / NumPy 實現,Matrix 以 numba 加速(無 numba 時退回純 Python),不需要額外 extras。`backtest` extra 保留為空以兼容舊配置,`uv sync --extra backtest` 與 `BACKEND_EXTRAS=legacy-cpu backtest` 仍可正常執行。

---

## 更新代碼(已部署用戶必讀)

拉取新版本只需一條命令:

```bash
git pull
```

**整個 `data/` 目錄都不納入 git** —— 行情日K、三大法人、融資融券、基本面、自選清單與分組、監控規則與觸發記錄,全部是程式執行時產生或拉取的使用者資料,`git pull` 物理上無法影響它們。新使用者首次啟動後,請到面板 **設定 → 資料來源 → 台股歷史日 K 資料庫** 下載歷史資料包(GitHub Release `data-daily-2026-09-10`, 範圍 2024-01-02 ～ 2026-09-10),之後的日常更新由排程自動處理。

> ⚠️ **切勿使用以下命令"解決衝突"或"清理",它們會一次性刪光 `data/` 下所有未被 git 跟蹤的數據:**
> - `git clean -fdx`(最危險,會刪掉所有 `.gitignore` 忽略的文件)
> - `git reset --hard`
> - 直接刪除整個項目文件夾重新 `git clone`
>
> 若 `git pull` 報衝突,通常是本地誤改了被跟蹤的文件,請先 `git stash` 暫存再 pull,或單獨聯繫作者,不要直接執行上面的命令。

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
- 手動放進策略目錄、檔名與 Python 模組同名的策略檔（例如 `json.py`），會在整個後端行程中取代該模組；透過界面保存的策略不會。
- 自訂歷史窗口策略（`filter_history`）在回測時收到整段資料，只能用按股票的時序運算，寫法見 `backend/app/strategy/prompts/strategy-guide.md`。
- 因子挖掘的工作管理器先寫入完成狀態、再寫入最後一筆事件，讀取端可能短暫看到「已完成」但事件還沒到齊。這是本次更新之前就有的問題，相關測試偶爾失敗。

---

## 訪問密碼設置(公網部署必讀)

面板部署在公網服務器時,首次設置訪問密碼有限制 —— **必須從本機或內網訪問**,以防公網上陌生人搶先設置密碼鎖死你的面板。

如果你在公網瀏覽器直接打開頁面,會看到提示:

> 首次設置密碼僅允許本機或內網訪問,請通過 SSH/本地瀏覽器操作

有兩種方式解決,任選其一。

### 方式一:環境變量預置密碼(最簡單,推薦)

在 `.env` 文件(或 Docker / 系統環境變量)裡設置 `AUTH_PASSWORD`:

```bash
AUTH_PASSWORD='你的密碼'
```

然後重啟服務。啟動時會自動:

1. 讀取 `AUTH_PASSWORD`
2. 用 PBKDF2 哈希後寫入 `auth.json`(`chmod 600`,只存哈希不存明文)
3. **之後這個環境變量就不再被讀取** —— 是一次性的初始化

設完後即可用公網地址 + 這個密碼正常登錄。後續改密碼請用頁面 UI(`設定 → 帳戶安全`),不受環境變量影響。

**注意事項:**

- **密碼至少 6 位**,否則會被跳過並記一條 warning 日誌
- **僅在未設過密碼時生效**。已設過密碼後,改這裡不會覆蓋(避免重啟時重置你在 UI 改的密碼)
- 密碼建議使用單引號包裹，避免 Docker Compose 插值 `$VAR`；啟動時也會從只讀掛載的原始 `.env` 初始化，兼容已有的未加引號配置
- `.env` 文件權限保持 `600`,**不要提交到 Git**
- 明文密碼只存在於 `.env` / 環境變量中,落盤的是哈希,安全性等同 `auth.json`

**重置密碼(忘密碼時):** 刪除或清空 `data/user_data/auth.json`,重啟服務,會回到"未設密碼"狀態,此時 `AUTH_PASSWORD` 會重新生效。

```bash
rm data/user_data/auth.json   # 停服後執行,清空後重啟
```

### 方式二:SSH 端口轉發

不用改配置,在你**自己電腦**的終端執行(不是服務器上):

```bash
ssh -L 3018:127.0.0.1:3018 用戶名@服務器IP
```

例如服務器是 `123.45.67.89`、用戶名 `root`、面板端口 `3018`:

```bash
ssh -L 3018:127.0.0.1:3018 root@123.45.67.89
```

保持這個 SSH 連接**不要關**,然後在**自己電腦的瀏覽器**打開 `http://127.0.0.1:3018`。此時後端看到的客戶端 IP 是 `127.0.0.1`(本機),能通過校驗,正常顯示設置密碼界面。

**設完密碼後**,SSH 連接可以斷開 —— 密碼已存進服務器,之後直接用公網地址 + 剛設的密碼訪問即可。

> 如果用 `PORT` 改過端口(比如 `PORT=8080`),兩處都要替換:`ssh -L 8080:127.0.0.1:8080 root@IP`。

### 兩種方式怎麼選

| | 環境變量 | SSH 轉發 |
|---|---|---|
| 操作 | 改一行配置 + 重啟 | 一條 ssh 命令 |
| 需要改配置 | 是 | 否 |
| 適合 | Docker / 自動化部署 / 不熟 SSH | 臨時設密碼 / 能 SSH 到服務器 |
| 後續改密碼 | UI(`設定 → 帳戶安全`) | 同左 |

推薦**方式一(環境變量)**,一次配置即可,Docker 部署尤其方便。

### 原理說明

- **為什麼限制本機/內網?** 面板部署到公網後,任何人都能訪問 URL。如果不限制,攻擊者可以在你之前打開頁面、設置一個密碼,把你的面板鎖死。
- **本機/內網如何判斷?** 後端檢查客戶端 IP 是否屬於 `127.0.0.1 / ::1 / 10.x / 192.168.x / 172.16-31.x`。
- **SSH 轉發為什麼有效?** `-L` 把本機端口通過 SSH 隧道轉發到服務器的 `127.0.0.1`,等同於在服務器本地訪問,客戶端 IP 變成 `127.0.0.1`,通過校驗。
- **反向代理注意:** 若面板在 Nginx 等反代之後,需正確配置 `X-Forwarded-For` 頭,後端據此取真實客戶端 IP。
