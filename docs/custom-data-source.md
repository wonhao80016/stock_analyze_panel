# 自定義數據源接入

本項目默認使用內建數據源。自定義數據源是一個可選擴展: 外部 HTTP 服務負責取數和整理, 本項目只把返回結果映射成內部標準字段, 然後複用現有存儲、指標、enriched、策略和前端展示邏輯。

## 支持範圍

當前自定義源支持四類數據:

| 數據集 | 配置名 | 說明 |
| --- | --- | --- |
| 日K | `daily` | 批量返回一組股票在指定區間內的日K |
| 除權因子 | `adj_factor` | 批量返回一組股票的復權因子 |
| 實時行情 | `realtime` | 返回全市場快照,用於盤中 enriched 增量計算 |
| 分鐘K | `minute` | 返回 1m 分鐘K(需映射出 symbol / datetime / OHLC / 量額) |

深度盤口(depth5)暫無數據集契約,仍由內建數據源提供。

YAML 仍可寫 `financial` 數據集,設置頁的「試拉測試」也能試拉它,但目前沒有任何同步流程會呼叫它,寫了不會把財務數據帶進系統。

## 配置位置

把 YAML 放到運行數據目錄下:

```text
data/data_sources/*.yaml
```

Dev 模式下，默認位置是項目根目錄的 `data/`；Docker 部署中，項目的 `data/` 會掛載為容器內的 `/app/data`。可通過 `DATA_DIR` 覆蓋。

修改 YAML 後可在「設置 -> 數據源」點擊「重新加載」,或調用:

```bash
curl -X POST http://127.0.0.1:3018/api/settings/data-sources/reload
```

## 最小 YAML

```yaml
name: mock_source
display_name: "Mock 自定義數據源"
auth:
  type: none

datasets:
  daily:
    url: http://127.0.0.1:3021/daily
    method: POST
    batch: 100
    rpm: 200
    response_path: data
    field_map:
      ts_code: symbol
      trade_date: date
      open: open
      high: high
      low: low
      close: close
      vol: volume
      amt: amount
    transforms:
      date: "parse_date(value, '%Y-%m-%d')"

  adj_factor:
    url: http://127.0.0.1:3021/adj_factor
    method: POST
    batch: 100
    rpm: 200
    response_path: data
    field_map:
      ts_code: symbol
      trade_date: trade_date
      factor: ex_factor
    transforms:
      trade_date: "parse_date(value, '%Y-%m-%d')"

  realtime:
    url: http://127.0.0.1:3021/realtime
    method: GET
    rpm: 60
    response_path: data
    field_map:
      ts_code: symbol
      name: name
      last: last_price
      pre_close: prev_close
      open: open
      high: high
      low: low
      vol: volume
      amt: amount
      pct: change_pct
      amount_change: change_amount
      amplitude: amplitude
      turnover: turnover_rate
    units:  # mock 源返回小數; 上游返回百分數的字段改寫 percent
      change_pct: decimal
      amplitude: decimal
      turnover_rate: decimal
```

## 字段契約

### daily 必填

| 內部字段 | 含義 |
| --- | --- |
| `symbol` | 標準代碼,如 `000001.SZ` |
| `date` | 交易日 |
| `open` / `high` / `low` / `close` | 不復權 OHLC |
| `volume` | 成交量 |
| `amount` | 成交額 |

### adj_factor 必填

| 內部字段 | 含義 |
| --- | --- |
| `symbol` | 標準代碼 |
| `trade_date` | 除權日期 |
| `ex_factor` | 復權因子 |

### realtime 必填

| 內部字段 | 含義 |
| --- | --- |
| `symbol` | 標準代碼 |
| `last_price` | 最新價 |
| `prev_close` | 昨收 |
| `open` / `high` / `low` | 當日 OHLC |
| `volume` | 成交量 |

建議實時接口額外提供 `amount`、`change_pct`、`change_amount`、`amplitude`、`turnover_rate`、`name`。缺失時部分字段會由 pipeline 回算,但精度取決於可用輸入。

`change_pct`、`amplitude`、`turnover_rate` 的契約是小數制,例如 `0.0366` 表示 `3.66%`。realtime 數據集映射了其中任何一個字段,就必須在 `units` 明確宣告它的單位,未宣告就拒絕載入;可宣告的字段只有這三個,值只能是:

| `units` 值 | 含義 | 換算 |
| --- | --- | --- |
| `decimal` | 小數制,`0.0366` 表示 `3.66%` | 不換算 |
| `percent` | 百分數,`3.66` 表示 `3.66%` | 除以 100 |

系統不會依數值大小猜測單位,也沒有預設單位。realtime 數據集映射了這三個字段之一卻沒有宣告、或宣告了其他字段或其他值時,重新加載會在「設置 -> 數據源」列出錯誤,該數據源不會載入,「試拉測試」也會失敗;在設置頁存檔時會先做同樣的檢查,不通過就不寫檔並顯示錯誤。載入失敗的數據源仍可在設置頁打開編輯、補上 `units` 後重新存檔。同一字段已在 `transforms` 寫了 `value / 100` 又宣告 `percent` 屬於重複換算,同樣會被拒絕:兩者擇一即可。「試拉測試」的預覽顯示的是按 `units` 換算後的值。

### 舊設定的遷移

舊版會依數值大小猜測是不是百分數,現在改為明確宣告。升級後,凡是 realtime 映射了 `change_pct`、`amplitude` 或 `turnover_rate` 卻沒有 `units` 的舊 YAML 都會拒絕載入,請依接口實際返回值補上:

```yaml
    units:
      change_pct: percent   # 接口返回 3.66 表示 3.66%
      turnover_rate: decimal  # 接口返回 0.05 表示 5%
```

舊設定若已用 `transforms` 把百分數除以 100,對應字段宣告 `decimal`,或刪掉該 transform 改宣告 `percent`。

## 請求約定

- `daily` / `adj_factor` 會按 `batch` 切分 symbols。
- POST 請求會發送 JSON body: `symbols`、`start_time`、`end_time`。
- GET 請求會發送 query 參數: `symbols=000001.SZ,600000.SH`。
- `realtime` 必須是全市場快照接口,不支持逐個 symbol 拉實時行情。

可通過這些字段改參數名:

```yaml
symbols_param: symbols
start_param: start_time
end_param: end_time
```

分鐘數據源如果需要區分資產類型或週期，可繼續配置：

```yaml
asset_type_param: asset_type
freq_param: period
```

配置後，分鐘請求會分別傳入 `stock` / `etf` / `index` 和 `1m`；留空時不向上游發送這兩個參數，以兼容已有數據源。

### 請求超時

每個數據集可單獨配置請求超時（秒），默認 30：

```yaml
timeout: 60
```

留空或省略時用默認 30 秒，可配置範圍為大於 0 且不超過 300 秒；該值對數據同步與「試拉測試」均生效。在設置頁編輯數據源時可在「超時」輸入框修改（與 批量 / RPM / 響應路徑 同行）。「試拉測試」直接使用當前表單內容，新建數據源或尚未保存的修改也可測試。

## 鑑權

支持三種簡單鑑權:

```yaml
auth:
  type: bearer
  token_env: MY_DATA_TOKEN
```

```yaml
auth:
  type: header
  header: X-Token
  token_env: MY_DATA_TOKEN
```

```yaml
auth:
  type: query
  param: token
  token_env: MY_DATA_TOKEN
```

Token 可以放在系統環境變量或項目 `.env` 中。

## 聯調流程

1. 啟動 mock 數據源:

```bash
cd docs/examples/custom-data-source
python mock_server.py
```

2. 複製示例配置:

```bash
mkdir -p data/data_sources
cp docs/examples/custom-data-source/mock_source.yaml data/data_sources/mock_source.yaml
```

   示例的 realtime 映射了比例字段,若複製的檔案還沒有 `units`,請照上方「最小 YAML」補上(mock 源返回小數制,三個字段都宣告 `decimal`)。

3. 在「設置 -> 數據源」點擊「重新加載」。

4. 使用「試拉測試」選擇 `mock_source` 和 `daily` / `adj_factor` / `realtime`。

5. 保存數據源選擇:

- 日K: `mock_source`
- 除權因子: `same_as_daily` 或 `mock_source`
- 實時行情: `mock_source`

6. 觸發同步或開啟實時行情。

## 常見錯誤

| 現象 | 處理 |
| --- | --- |
| 列表裡沒有 custom 源 | 檢查 YAML 是否放在 `data/data_sources/` 並點擊重新加載 |
| errors 提示 missing mapped fields | `field_map` 沒映射到必填內部字段 |
| 試拉 rows 為 0 | 檢查 `response_path` 是否指向數組 |
| 日期列全為空 | 檢查 `parse_date` 的格式是否和返回值一致 |
| 實時行情沒刷新 | 確認實時數據源已保存為 custom,且返回全市場快照 |

## 用 AI 生成映射配置

如果你的數據源 API 文檔比較複雜,可以把 API 文檔和返回示例丟給 AI,讓它幫你生成 `field_map` 和 YAML 配置。

### 操作步驟

1. 從你的數據源獲取 API 文檔(接口地址、請求方式、返回字段說明)
2. 試拉一次,拿到返回的 JSON 示例
3. 把下面的 prompt 模板 + API 文檔 + JSON 示例一起發給 AI
4. 把 AI 生成的 YAML 貼到 `data/data_sources/xxx.yaml`
5. 在設置頁點「重新加載」,再「試拉測試」驗證

### Prompt 模板

複製以下內容發給 AI(替換方括號部分):

```text
我在配置一個自定義數據源接入股票面板。請根據我的 API 文檔和返回示例,生成 YAML 配置。

要求:
1. 輸出標準 YAML 配置,包含 name / display_name / auth / datasets
2. 每個數據集的 field_map 把我的接口字段名映射到內部字段名
3. 日期類字段如果格式不是 YYYY-MM-DD, 加上 transforms 裡的 parse_date
4. 只配置我能提供的接口, 不存在的數據集不要寫

內部字段對照表:

日K (daily):
  symbol = 股票代碼, 格式 000001.SZ / 600000.SH
  date = 交易日期
  open / high / low / close = OHLC
  volume = 成交量
  amount = 成交額

除權因子 (adj_factor):
  symbol = 股票代碼
  trade_date = 除權日期
  ex_factor = 復權因子

實時行情 (realtime):
  symbol = 股票代碼
  last_price = 最新價
  prev_close = 昨收價
  open / high / low = 當日 OHLC
  volume = 成交量
  amount = 成交額
  change_pct = 漲跌幅 (小數, 0.0366 = 3.66%)
  change_amount = 漲跌額
  amplitude = 振幅 (小數)
  turnover_rate = 換手率 (小數, 0.05 = 5%)
  上面三個比例字段若上游返回百分數 (5 表示 5%), 在 realtime 下加 units, 例如 units: {turnover_rate: percent}; 小數制則宣告 decimal。realtime 映射了這三個字段卻沒寫 units 會拒絕載入

分鐘K (minute):
  symbol = 股票代碼
  datetime = 時間戳 (YYYY-MM-DD HH:MM:SS)
  open / high / low / close = OHLC
  volume = 成交量
  amount = 成交額

=== 我的 API 文檔 ===
[把你的接口文檔貼這裡: URL / 請求方式 / 參數 / 返回字段說明]

=== 返回 JSON 示例 ===
[把試拉的 JSON 返回貼這裡]
```

AI 會輸出類似這樣的結果:

```yaml
name: my_source
display_name: "我的數據源"
auth:
  type: bearer
  token_env: MY_API_TOKEN

datasets:
  daily:
    url: https://api.example.com/kline
    method: POST
    batch: 100
    rpm: 200
    response_path: data.list
    field_map:
      ts_code: symbol
      trade_date: date
      open: open
      vol: volume
    transforms:
      date: "parse_date(value, '%Y%m%d')"
```

把這段 YAML 保存為 `data/data_sources/my_source.yaml`,然後在設置頁重新加載即可。
