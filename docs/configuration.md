# 配置詳解

所有配置從根目錄 `.env` 讀取(複製 `.env.example` 開始),也可在面板 **設定** 頁面修改。本文件解釋每個配置項的作用。

部署相關配置(端口/密碼/老 CPU 兼容)的實操見 [deployment.md](./deployment.md)。

---

## 資料來源設定

預設資料來源是**台灣官方資料源(TWSE / TPEx)**,整合台灣證券交易所與證券櫃檯買賣中心的公開資料,提供日K歷史行情、即時報價、標的清單與基本面資訊,**不需要任何 API Key**,`.env` 也不需要為它填任何東西。

資料集的提供方在面板 **設定 → 資料來源** 逐項切換;沒有個別指定的資料集一律由台灣官方資料源提供。系統同時支援插件化接入第三方資料來源(YAML 宣告自有介面見 [custom-data-source.md](./custom-data-source.md),插件開發見 [plugin-development.md](./plugin-development.md))。

歷史日 K 需要先在本機建立,做法見 [操作說明書 → 歷史日 K 與 GitHub Release 資料包](../操作說明書.md#15-歷史日-k-與-github-release-資料包)。

---

## AI(可選)

AI 為選配功能,用在以下幾個地方,全部要手動觸發:台股選股頁把中文條件翻譯成篩選欄位、多股比較頁對已呈現的數據做客觀解讀、個股頁的 AI 研究報告,以及自訂訊號的 AI 生成。**所有設定留空即跳過**,不影響核心功能。支援任意 OpenAI 相容介面,也可以改用本機的 Codex CLI。

`AI_BASE_URL` 與 `AI_MODEL` 沒有內建預設值。使用 OpenAI 相容介面時,API 位址、API Key、模型任一項留空,AI 就視為未設定,相關功能回報「未設定 AI 服務」,不會連到任何預設位址。下面以 DeepSeek 為例:

```ini
AI_PROVIDER=openai_compat              # openai_compat | openai | codex_cli
AI_BASE_URL=https://api.deepseek.com/v1   # 留空 = 關閉 AI;OpenAI 為 https://api.openai.com/v1
AI_API_KEY=                            # 留空 = 關閉 AI
AI_MODEL=deepseek-chat                 # 留空 = 關閉 AI
```

| 配置項 | 說明 |
| :--- | :--- |
| `AI_PROVIDER` | `openai_compat`(OpenAI 兼容,支持 DeepSeek / 通義 / OpenAI 等)、`openai`(OpenAI 官方介面,可另設推理強度)或 `codex_cli`(本機已登入的 Codex CLI,不需要 API 位址與 Key) |
| `AI_BASE_URL` | 接口地址,無預設值,留空則關閉 AI 功能。如 DeepSeek `https://api.deepseek.com/v1`、OpenAI `https://api.openai.com/v1` |
| `AI_API_KEY` | 留空則關閉 AI 功能 |
| `AI_MODEL` | 模型名,無預設值,留空則關閉 AI 功能。如 `deepseek-chat` |
| `AI_CODEX_COMMAND` / `AI_CODEX_REASONING_EFFORT` | `codex_cli` 使用的命令(預設 `codex`)與推理強度(預設留空) |
| `AI_MAX_OUTPUT_TOKENS` / `AI_CONTEXT_WINDOW` | 單次輸出上限(預設 8192)與輸入上下文窗口(預設 64000,約 token),輸入超出時直接報錯 |
| `AI_USER_AGENT` | 呼叫 AI 介面時的 User-Agent,預設為瀏覽器風格字串 |

目前沒有每日 token 預算或用量上限的設定。

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
ALLOW_USER_PYTHON_STRATEGIES=false   # true = 載入 data/strategies/{custom,ai,composite} 中的使用者 Python 策略
```

自訂策略、AI 策略與疊加策略都存成 `data/strategies/{custom,ai,composite}` 下的 `.py`,載入時會在後端行程內以完整權限執行。載入前的 AST 檢查只是語法提示,可以被繞過,不是安全防護。因此預設關閉:

面板與 API 都沒有儲存策略原始碼的入口,使用者策略檔只能手動放進上述目錄。這個開關只決定後端與回測 worker 是否載入它們:

- 關閉時:上述目錄中的 `.py` 不會執行,會記入後端 log,並列在 `GET /api/strategies` 回傳的 `load_errors`。內建策略照常運作。
- 開啟時:改成 `true` 並重啟後端才生效,目錄中的策略檔會在後端與回測 worker 載入。

只有在你信任能寫入 `data/strategies/` 的每一個人與程式時才開啟。

---

## 後端依賴 Extras(可選)

```ini
BACKEND_EXTRAS=             # 留空默認;legacy-cpu 兼容老 CPU
```

老 CPU 無 AVX2/FMA 支持時設為 `legacy-cpu`,會給 Polars 切到 `rtcompat` 運行時;回測不需要額外 extras。Docker 構建和 `./dev.sh` / `.\dev.ps1` 都會讀取此值並同步依賴。詳見 [deployment.md → 老 CPU 兼容](./deployment.md#老-cpu-兼容avx2fma-缺失)。

---

## 配置優先級

同一個配置項有多個來源時,由高到低:

1. **面板設定頁**(`設定 → ...`):UI 修改後立即生效。AI 設定與 Key、插件 Key 存在 `data/user_data/secrets.json`,其他偏好存在 `data/` 下的偏好檔
2. **環境變量**:Docker / 系統環境變量,會蓋過 `.env` 中的同名項
3. **`.env` 文件**:啟動時讀取
4. 程式內建預設值

面板一旦儲存過 AI 設定,之後改環境變量或 `.env` 都不會生效;要改用環境變量,先在 **設定 → AI 設定** 清除設定(`DELETE /api/settings/ai`)。

> 多數設定可在面板設定頁修改,無需手動編輯 `.env`。`.env` 與 `secrets.json` 都含密鑰,不要提交到 git。
