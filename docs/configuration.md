# 配置詳解

所有配置從根目錄 `.env` 讀取(複製 `.env.example` 開始),也可在面板 **設定** 頁面修改。本文件解釋每個配置項的作用。

部署相關配置(端口/密碼/老 CPU 兼容)的實操見 [deployment.md](./deployment.md)。

---

## 資料來源設定

預設資料來源是**台灣官方資料源(TWSE / TPEx)**,整合台灣證券交易所與證券櫃檯買賣中心的公開資料,提供日K歷史行情、即時報價、標的清單與基本面資訊,**不需要任何 API Key**,`.env` 也不需要為它填任何東西。

資料集的提供方在面板 **設定 → 資料來源** 逐項切換;沒有個別指定的資料集一律由台灣官方資料源提供。系統同時支援插件化接入第三方資料來源(YAML 宣告自有介面見 [custom-data-source.md](./custom-data-source.md),插件開發見 [plugin-development.md](./plugin-development.md))。

歷史日 K 需要先在本機建立,做法見 [操作說明書 → 歷史日 K 與 GitHub Release 資料包](../操作說明書.md#15-歷史日-k-與-github-release-資料包)。

### 選配:舊 TickFlow 資料來源

```ini
TICKFLOW_API_KEY=              # 選配;一般台股使用者留空即可
```

這是專案原本的 A 股資料來源,台股功能不依賴它。只有在你確實要接這個來源時才需要填,留空不影響任何台股功能。

---

## AI(可選)

AI 為選配功能,目前用在兩個地方:台股選股頁把中文條件翻譯成篩選欄位,以及多股比較頁對已呈現的數據做客觀解讀。兩者都要手動觸發。**所有設定留空即跳過**,不影響核心功能。支援任意 OpenAI 相容介面。

`AI_BASE_URL` 與 `AI_MODEL` 沒有內建預設值。API 位址、API Key、模型任一項留空時,AI 視為未設定,相關功能回報「未設定 AI 服務」,不會連到任何預設位址。下面以 DeepSeek 為例:

```ini
AI_PROVIDER=openai_compat              # openai_compat | ollama
AI_BASE_URL=https://api.deepseek.com/v1   # 留空 = 關閉 AI;OpenAI 為 https://api.openai.com/v1
AI_API_KEY=                            # 留空 = 關閉 AI
AI_MODEL=deepseek-chat                 # 留空 = 關閉 AI
AI_DAILY_TOKEN_BUDGET=500000           # 每日 token 預算上限
```

| 配置項 | 說明 |
| :--- | :--- |
| `AI_PROVIDER` | `openai_compat`(OpenAI 兼容,支持 DeepSeek / 通義 / OpenAI 等)或 `ollama`(本地模型) |
| `AI_BASE_URL` | 接口地址,無預設值,留空則關閉 AI 功能。如 DeepSeek `https://api.deepseek.com/v1`、OpenAI `https://api.openai.com/v1` |
| `AI_API_KEY` | 留空則關閉 AI 功能 |
| `AI_MODEL` | 模型名,無預設值,留空則關閉 AI 功能。如 `deepseek-chat` |
| `AI_DAILY_TOKEN_BUDGET` | 每日 token 預算,超限後當日不再調用 |

也可以直接在面板 **設定 → AI 設定** 填寫與測試,不必手動編輯 `.env`。

---

## 服務

```ini
HOST=0.0.0.0          # 開發服務監聽地址 / Docker 主機綁定地址
PORT=3018             # 開發後端端口 / Docker 主機映射端口
LOG_LEVEL=INFO        # DEBUG | INFO | WARNING | ERROR
```

- `HOST`:`0.0.0.0` 監聽所有網卡(容器/公網部署需要);僅本機用可設 `127.0.0.1`
- `PORT`:默認 `3018`;開發模式兼容顯式的 `BACKEND_PORT` 覆蓋,改端口後 SSH 轉發命令也要同步改
- `LOG_LEVEL`:排查問題時改 `DEBUG`

---

## 數據

```ini
DATA_DIR=./data       # Parquet / DuckDB 數據存儲目錄
```

整個 `data/` 目錄都不納入 git —— 行情日K、三大法人、融資融券、基本面、自選清單與分組、監控規則與觸發記錄,全部是程式執行時產生或拉取的使用者資料。

如需遷移數據,直接拷貝整個 `data/` 目錄即可。詳見 [deployment.md → 更新代碼](./deployment.md#更新代碼已部署用戶必讀)。

---

## 訪問密碼(公網部署)

```ini
AUTH_PASSWORD='你的密碼'  # 至少 6 位;僅首次生效,已設過則不覆蓋
```

未設訪問密碼時,出於安全考慮面板**只接受本機(`127.0.0.1` / `::1`)的請求**,區網與公網一律拒絕(防他人搶先設置鎖死面板)。區網、公網或 Docker 部署請通過此環境變量預置首個密碼。
密碼建議使用單引號包裹，Docker 啟動時會把整個原始 `.env` 只讀掛載到容器內 `/app/.env`，兼容已有的未加引號配置。容器可以讀取其中的密鑰但不能修改該文件，請保持主機文件權限為 `600` 並僅運行可信鏡像。

詳細步驟、SSH 轉發方案、重置密碼方法見 [deployment.md → 訪問密碼設置](./deployment.md#訪問密碼設置公網部署必讀)。

---

## 使用者 Python 策略(預設關閉)

```ini
ALLOW_USER_PYTHON_STRATEGIES=false   # true = 允許儲存並載入自訂 / AI / 疊加策略的 .py
```

自訂策略、AI 策略與疊加策略都存成 `data/strategies/{custom,ai,composite}` 下的 `.py`,載入時會在後端行程內以完整權限執行。存檔前的 AST 檢查只是語法提示,可以被繞過,不是安全防護。因此預設關閉:

- 關閉時:`/api/strategies/code/save`、`/ai/save`、`/composite/save` 回 403,不寫入任何檔案;挖掘出的因子候選也無法發佈成新策略(發佈既有策略不受影響)。上述目錄中已有的 `.py` 不會執行,會記入後端 log,並列在 `GET /api/strategies` 與 `POST /api/strategies/reload` 回傳的 `load_errors`。內建策略照常運作;已存在的使用者策略檔仍可查看原始碼或刪除。
- 開啟時:改成 `true` 並重啟後端才生效。三個存檔 API 與挖掘候選發佈 `POST /api/backtest/mining/runs/{run_id}/candidates/{signature}/publish` 只接受本機請求(對端與 Host 都必須是 `127.0.0.1`、`::1` 或 `localhost`),區網、公網以及經反向代理轉入的遠端請求一律回 403。

只有在你信任能寫入 `data/strategies/` 的每一個人與程式時才開啟。產生與校驗程式碼的端點(`/build`、`/build/stream`、`/ai/generate`、`/code/validate`)不寫檔也不執行,開關關閉時仍可使用。

---

## 後端依賴 Extras(可選)

```ini
BACKEND_EXTRAS=             # 留空默認;legacy-cpu 兼容老 CPU
```

老 CPU 無 AVX2/FMA 支持時設為 `legacy-cpu`,會給 Polars 切到 `rtcompat` 運行時;回測不需要額外 extras。Docker 構建和 `./dev.sh` / `.\dev.ps1` 都會讀取此值並同步依賴。詳見 [deployment.md → 老 CPU 兼容](./deployment.md#老-cpu-兼容avx2fma-缺失)。

---

## 配置優先級

1. **面板設定頁**(`設定 → ...`):UI 修改後立即生效,持久化到 `data/`
2. **`.env` 文件**:啟動時讀取
3. **環境變量**:Docker / 系統環境變量,優先級最高

> 多數設定可在面板設定頁修改,無需手動編輯 `.env`。僅 AI Key、API Key 等敏感項建議放 `.env`(不提交到 git)。
