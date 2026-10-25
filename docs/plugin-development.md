# 數據源插件開發指南

數據源插件是可選的第三方行情來源,以獨立目錄放在 `backend/app/plugins/` 下。倉庫本身**不附帶任何插件**,
`backend/app/plugins/` 只是投放目錄(Git 不追蹤空目錄,新 clone 的倉庫裡可能沒有它,需要自己建立)。
沒有插件時完全不影響主功能;只想接一個 HTTP 介面時,用 YAML 宣告的自定義數據源更簡單,見
[custom-data-source.md](./custom-data-source.md)。

## 能切換到插件的數據集

台股的日K與除權因子固定由台灣官方資料源提供:`PUT /api/settings/preferences/data-providers` 拒絕把
`daily_data_provider`、`adj_factor_provider` 改成官方以外的值,設定頁也不提供切換。插件實際會被呼叫的只有:

| 數據集 | 呼叫方 | 選了插件、但插件沒宣告這個數據集時 |
|---|---|---|
| `realtime` | `services/quote_service.py` 的行情輪詢 | 記一次 warning,行情輪詢跳過,直到改選有 `realtime` 的來源;**不會**改用官方即時行情 |
| `minute` | `services/kline_sync.py` 的分鐘 K、分時圖與分時訊號監控 | 回傳空表;分時訊號監控回報「台股沒有分鐘資料源」並停用 |

`financial` 可以宣告也可以在偏好中保存,但目前沒有任何流程會呼叫它。只宣告 `daily`、`adj_factor` 的來源無法被設成目前資料來源。

## 快速上手

一個插件 = 一個目錄 + 一個 `plugin.yaml` 清單:

```
backend/app/plugins/<your_plugin>/
├── plugin.yaml          # 清單(必需)
├── provider.py          # Provider 實現(必需)
├── requirements.txt     # runtime: python 時,設定頁「安裝依賴」使用
├── package.json         # runtime: node 時,設定頁「安裝依賴」在此目錄執行 npm install
└── ...                  # 其他橋接文件(按需)
```

### plugin.yaml 字段

```yaml
name: my_source                          # 唯一標識, 只允許 [a-z0-9_], 也是 provider name
display_name: "我的數據源"                 # 設置頁顯示名
runtime: python                          # 運行時類型: node | python | none
entry: app.plugins.my_source.provider:MyProvider   # provider 類的導入路徑
check: app.plugins.my_source.bridge:availability   # 可用性檢測函數(可選)
datasets: [realtime, minute]             # 支持的數據集
api_key_env: MY_SOURCE_API_KEY           # (可選)聲明後設置頁提供 Key 輸入框
hidden: false                            # (可選)true = 跳過註冊,也不在設置頁展示
description: "數據源描述"
install_hint: "pip install xxx"          # 未裝依賴時顯示的安裝提示
```

#### api_key_env(界面配置 API Key)

聲明 `api_key_env` 的插件可以在設置頁的數據源卡片中直接填寫 Key,採「先探後存」:

1. entry 模塊需提供模塊級 `probe_api_key(key) -> (ok, reason)`,
   後端(`POST /api/settings/plugin-key`)用候選 Key 實探一次, **無效不落盤**
2. 有效則寫入 `data/user_data/secrets.json` 的 `{name}_api_key` 字段
   (0600 權限, 優先級高於環境變量)
3. 保存後自動重載數據源註冊表, 插件即刻變為可切換
4. 插件取 Key 用 `secrets_store.get_env_backed_secret("{name}_api_key", api_key_env)`:
   先讀 secrets.json,沒有才讀進程環境變量 `api_key_env`(只讀 `os.environ`,不會讀 `.env` 文件)

`DELETE /api/settings/plugin-key/{name}` 清除界面配置的 Key,環境變量中的同名變量仍然生效。

### runtime 字段說明

| runtime | 含義 | 設定頁「安裝依賴」的行為 |
|---|---|---|
| `python` | 純 Python 依賴 | 以 `uv pip install`(沒有 uv 時用 `python -m pip`)把 `requirements.txt` 裝進後端虛擬環境 |
| `node` | 需要 Node.js 運行時 | 在插件目錄執行 `npm install --omit=dev` |
| `none` | 無額外依賴 | 不需要安裝,例如純 HTTP API 源 |

安裝與卸載的端點是 `POST` / `DELETE /api/settings/plugins/{name}/install`。是否可用仍由 `check` 函數判斷,`runtime` 只決定安裝方式。

### check 函數

插件自己負責檢測依賴是否已安裝。後端載入插件時會調用此函數:

```python
# app/plugins/my_source/bridge.py
def availability() -> tuple[bool, str]:
    """返回 (是否可用, 原因)。不拋異常。"""
    try:
        import some_sdk  # noqa: F401
        return True, "ok"
    except ImportError:
        return False, "未安裝 some_sdk, 運行: pip install some_sdk"
```

- 沒有 `check` 字段 → 視為可用
- **可用** → 插件註冊進路由表, 設置頁可切換
- **不可用** → 設置頁顯示插件卡片但灰顯, 展示 `install_hint`
- 聲稱可用但 import `entry` 失敗 → 標記為不可用, 不影響後端啟動

## Provider 接口契約

Provider 是一個普通 Python 類(無需繼承基類), 實現與 `GenericHTTPProvider`
(`backend/app/data_providers/custom/provider.py`)相同的方法簽名, 這樣 services 層的路由邏輯
零改動即可路由到插件。只需實現 `datasets` 中聲明的數據集。

```python
class MyProvider:
    name = "my_source"

    def __init__(self):
        self.config = MyConfig()  # 需有 .datasets 屬性(dict, key 是數據集名)

    def close(self) -> None:
        """清理資源(重建註冊表時會調)。"""

    def get_realtime(self) -> list[dict]:
        """全市場實時快照: 返回 list[dict], 每行含 symbol/last_price/prev_close/open/high/low/volume"""

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None, freq="1m") -> pl.DataFrame:
        """分鐘K: 返回 schema [symbol, datetime, open, high, low, close, volume, amount]"""
```

載入時後端會把 `builtin` 設為 `True`,所以插件不會出現在可編輯的自定義數據源列表中。

代碼必須是台股標準格式(`2330.TWSE`、`6488.TPEX`),成交量單位是股;`change_pct`、`turnover_rate` 等字段的口徑與
自定義數據源相同,見 [custom-data-source.md](./custom-data-source.md) 與 CONTRIBUTING §3.1。

### config.datasets 的作用

`provider_has_dataset(name, dataset)` 通過 `dataset in provider.config.datasets` 判斷,
services 層據此決定是否呼叫插件;未聲明時的行為見上方「能切換到插件的數據集」。

```python
class MyConfig:
    datasets = {"realtime": ..., "minute": ...}  # key 是數據集名, value 任意
```

## 路由機制(無需關心, 僅參考)

後端導入 `app.data_providers.custom.loader` 以及每次重載數據源時,`_load_builtin_plugins()` 掃描
`backend/app/plugins/`(目錄不存在就跳過):

1. 讀每個子目錄的 `plugin.yaml`
2. 調 `check` 函數檢測可用性
3. 可用 → 動態 import `entry` 指向的 Provider 類 → 註冊進 `_PROVIDERS`
4. 不可用 → 記錄狀態, 設置頁顯示但不可切換

註冊後, 插件和用戶 YAML 自定義源走**完全相同的路由路徑**(services 層的
`provider_has_dataset` / `get_provider` 調用), 無需額外集成代碼。
