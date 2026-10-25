
<div align="center">

# 📈 Itoy 的台股監控看板

[![聲明:個人開源](https://img.shields.io/badge/⚠️_聲明-個人開源-green?style=for-the-badge&labelColor=red)](https://github.com/Itoy1212/TWstockfor_tick-stock-panel)



**自架、零維運的台股「看市場 + 選股 + 監控」面板**

**資料來自 TWSE / TPEx 公開資料,不需要任何付費行情 API Key**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://react.dev/)
[![Deploy: Docker](https://img.shields.io/badge/Deploy-Docker-2496ed.svg)](./Dockerfile)

</div>

<div align="center">

**[主要功能](#-主要功能)** · **[快速開始](#-快速開始)** · **[文件導覽](#-文件導覽)** · **[技術棧](#️-技術棧)**

</div>


---

**本專案為個人開源專案,與台灣證券交易所、證券櫃檯買賣中心或任何商業資料服務均無官方關聯。僅供學習與研究使用。**

> ⚠️ 這是把公開資料整理成看得懂的畫面、並依策略選股的工具;建議僅供參考,不保證獲利,投資風險自負。
>
> **明確不做**:自動下單。AI 是選配功能,只在你主動點擊時才會呼叫。

---

## ✨ 主要功能

範圍是台灣上市、上櫃標的。資料在本機以 Parquet / DuckDB 儲存,歷史日 K 可從 GitHub Release 資料包一次匯入,之後由盤後排程自動更新。

| 頁面 | 一句話 |
| :--- | :--- |
| 📊 **看板** | 今日市場強弱、產業強弱、自選股動態、策略當日推薦、監控觸發記錄與台股資料狀態 |
| 🔍 **台股選股** | 全市場量化統計快照、產業輪動、客觀異常訊號、量價型態、常用篩選與技術面/法人籌碼條件 |
| 🎯 **策略庫** | 各策略當日選股與分級、多策略共振,以及選股後 T+1~T+20 的事後統計 |
| 📉 **策略回測** | 依台股交易成本與整股/零股規則,驗證策略在指定標的與期間的歷史表現 |
| ⭐ **自選股** | 多對多分組、表格/卡片檢視、自訂欄位、迷你日 K 與分時 |
| ⚖️ **多股比較** | 多檔並列的確定性比較表,AI 客觀解讀為選配且需手動觸發 |
| 📈 **個股頁面** | 歷史日 K、五檔盤口、三大法人、融資融券、核心籌碼因子與選配的 AI 研究報告 |
| 🚨 **監控中心** | 價格、量能、技術訊號、策略與板塊規則,命中可推播到 LINE 或 Telegram |

各功能的完整說明以 [操作說明書](./操作說明書.md) 為準。

---

## 🚀 快速開始

三種方式擇一,細節見 [操作說明書第 2 節](./操作說明書.md#2-安裝與啟動)。

- **桌面安裝包**:到 [Releases](https://github.com/Itoy1212/TWstockfor_tick-stock-panel/releases) 下載 Windows、macOS(Apple Silicon)或 Linux 安裝檔。
- **Docker**:

  ```bash
  cp .env.example .env       # 先在 .env 設定 AUTH_PASSWORD
  docker compose up --build  # 開啟 http://localhost:3018
  ```

- **原始碼開發模式**(Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`):

  ```bash
  cp .env.example .env
  ./dev.sh                   # Windows: .\dev.ps1
  ```

跑起來後走完引導,再到 **設定 → 資料來源 → 台股歷史日 K 資料庫** 下載歷史資料包即可開始使用。日常使用不需要任何 API Key。

---

## 📚 文件導覽

| 文件 | 內容 |
| :--- | :--- |
| [操作說明書](./操作說明書.md) | **功能正本**:逐頁操作、推播與 AI 設定、資料來源與更新、常見問題、資料與隱私 |
| [docs/deployment.md](./docs/deployment.md) | 部署方式(開發模式 / Docker)、舊 CPU 相容、更新程式、存取密碼 |
| [docs/configuration.md](./docs/configuration.md) | 所有 `.env` 設定項詳解 |
| [docs/taiwan-market-overview.md](./docs/taiwan-market-overview.md) | 台股(TWSE/TPEx)模組開發者指南 |
| [docs/taiwan-data-sources.md](./docs/taiwan-data-sources.md) | 台股資料來源與端點說明 |
| [docs/custom-data-source.md](./docs/custom-data-source.md) | 自訂資料來源接入與 YAML 設定 |
| [docs/plugin-development.md](./docs/plugin-development.md) | 資料來源插件開發規範 |
| [docs/secondary-development.md](./docs/secondary-development.md) | 二次開發、前端插槽、後端策略介面 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 貢獻、AI 開發與複審規範 |

---

## 🏗️ 技術棧

| 層 | 選型 |
| :--- | :--- |
| **後端** | FastAPI · Pydantic v2 · APScheduler · sse-starlette |
| **資料** | Polars(計算)· DuckDB(查詢)· Parquet(儲存)· numba(回測矩陣加速) |
| **資料來源** | TWSE / TPEx 官方公開資料 · 可插件化擴充(YAML 自訂源) |
| **AI**(選配) | OpenAI 或任何 OpenAI 相容介面 |
| **前端** | React 18 · Vite · TypeScript · Tailwind · TanStack Query · ECharts · dnd-kit |
| **桌面版** | PyInstaller · pywebview · Inno Setup(Windows 安裝包) |
| **部署** | Docker 兩階段建置,前端 dist 拷進後端映像檔,**單容器** |

---

## ⚠️ 免責聲明

本專案僅供**學習與研究**,**建議僅供參考,不保證獲利,投資風險自負**。資料正確性以各來源官方公告為準,系統不保證即時性與完整性。股市有風險,投資決策與後果由使用者自行承擔。

## 📄 License

[MIT](./LICENSE) © Itoy 的台股監控看板 contributors

本專案由 [tick-stock-panel](https://github.com/shy3130/tick-stock-panel) 改作而來,原專案為 A 股面板,本分支已改為台股定位。
