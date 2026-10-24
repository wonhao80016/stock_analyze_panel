# AI 策略生成精簡指南

市場為台股。只生成一個完整 Python 策略檔，直接輸出程式碼，不要輸出 Markdown 說明。

## 必須遵守

1. Polars/歷史策略限用 `polars/datetime`；矩陣策略限用 `numpy/app.backtest.matrix`。
2. AI 策略位於 `data/strategies/ai/`，`META.id` 用指定的 `ai_` ID。
3. 禁止檔案讀寫和 `open/exec/eval/compile/__import__/globals/locals/vars/dir/getattr/setattr/delattr/type/input`。
4. `META.params` 只放可調項：必填 `id/label/type/default`；數值項加 `min/max/step`，select 加 `options`。
5. `META.scoring` 只用真實數值欄位或 `ma20_bias`，權重和為 1.0；低值優先用 `"scoring_directions": {"欄位": "low"}`，勿用 `descending=False`。缺評分值的候選分數為 null、排最後；0 分有效。
6. `ENTRY_SIGNALS/EXIT_SIGNALS` 只選相關訊號，無匹配可為空。
7. `name`、`description`、`RULES`、參數 `label` 一律用繁體中文；`RULES` 至少 3 條核心邏輯。
8. 優先 Polars 向量化，避免逐列迴圈。
9. `META` 須為頂層字面量字典（可用 `META: dict = {...}`），禁止改名或動態建構。

## 檔案結構

```python
"""策略簡短描述"""
import polars as pl

META = {
    "id": "ai_xxxxxxxxxxxx",
    "name": "策略名稱",
    "description": "一句話說明策略邏輯",
    "tags": ["標籤"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 10,  # 元
        "amount_min": 5e7,  # 成交額(元)
        "exclude_new_days": 30,  # 交易日
        "boards": ["TWSE", "TPEX"],  # 上市/上櫃
    },
    "params": [],  # float/int/bool/select/date
    "scoring": {},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 20

RULES = """
1. 規則一
2. 規則二
3. 規則三
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
```

## 何時用 filter_history

`filter()` 只判斷當日。需要多日資料（近 N 天出現某事件、漲停後第 X 天、前高前低、連續下跌等）時用 `filter_history(df, params) -> pl.DataFrame`：

```python
LOOKBACK_DAYS = 8
EXECUTION_BACKEND = "python_history_legacy"
REQUIRED_FEATURES = {"ma20", "momentum_20d"}  # 須宣告讀取的欄位

def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    if df.is_empty() or "date" not in df.columns:
        return df
    hist = df.sort(["symbol", "date"]).with_columns([
        pl.col("close").shift(1).over("symbol").alias("_prev_close"),
    ])
    return hist.filter(pl.col("close") > pl.col("_prev_close"))
```

選股時 df 是目標日及前 `LOOKBACK_DAYS` 個交易日，回測時是整段：只用按 `symbol` 的 shift/rolling，不要 `df["date"].max()` 或不分日期的 rank/mean；回傳所有符合列。

## matrix_native 檔案結構

指定 `matrix_native` 時不得生成 `filter()` 或 `filter_history()`：

```python
import numpy as np
from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

META = {"id": "ai_matrix_example", "name": "矩陣範例", "description": "...",
        "asset_types": ["stock"], "timeframes": ["1d"], "params": [],
        "scoring": {}, "order_by": "score", "descending": True, "limit": 100}
EXECUTION_BACKEND = "matrix_native"

class ExampleMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "ma20"})

    def required_warmup_bars(self, params: dict) -> int:
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = market.close > matrix_feature(market, "ma20")
        return make_signal_matrix(market.shape, entry=entry.astype(np.uint8))

MATRIX_STRATEGY = ExampleMatrixStrategy()
```

- 讀 `ema*/macd_*/rsi_*/atr_*/kdj_*` 時暖機至少 `recursive_warmup_bars("欄位")`（ema60 為 337），起始幾根為空。
- 缺 K 用 `valid_rolling_*(值, np.isfinite(market.close), 窗口)`；`rolling_*` 窗內缺一根即 NaN。
- 可選 `required_coverage_fields(self, params)`：某交易日合格標的有值比例低於 50% 即拒絕，自查用 `session_coverage(market, "欄位")`。
- 可選 `limit_entries(self, entry, score, params)`：回傳同形布林遮罩，只能刪減進場。

date 參數是字串，先轉換：`_date.fromisoformat(raw) if isinstance(raw, str) else raw`。

## 常用欄位

代碼：`symbol` 如 `2330.TWSE`（上市）、`6488.TPEX`（上櫃）；篩市場用 `basic_filter.boards`。另有 `date`、`name`。
價格（元，前復權；`raw_*` 為原始價）：`open`, `high`, `low`, `close`, `raw_close`, `raw_high`, `raw_low`, `prev_close`, `change_amount`
小數（0.03 = 3%）：`change_pct`, `amplitude`, `momentum_5d/10d/20d/30d/60d`, `annual_vol_20d`
均線：`ma5`~`ma60`, `ema5`~`ema60`
技術指標：`macd_dif`, `macd_dea`, `macd_hist`, `boll_upper`, `boll_lower`, `kdj_k/d/j`, `rsi_6/14/24`, `atr_14`（hist=DIF−DEA）
量能：`volume`（股，1 張 = 1000 股）, `vol_ma5`, `vol_ma10`, `vol_ratio_5d`（倍）, `amount`（元）, `turnover_rate`（百分數，5 = 5%；分母為已發行普通股扣私募；只在各交易日發布後才有，ETF/TDR 為空）
其他：`high_60d`, `low_60d`, `consecutive_limit_ups`, `consecutive_limit_downs`；虛擬評分 `ma20_bias = close / ma20 - 1`
台股無 `total_shares`/`float_shares`，市值上下限被忽略。

## 常用訊號列

訊號列是布林值，用時加 `.fill_null(False)`：`signal_ma_golden_5_20`/`signal_ma_dead_5_20`、`signal_ma_golden_20_60`、`signal_macd_golden`/`signal_macd_dead`、`signal_ma20_breakout`/`signal_ma20_breakdown`、`signal_n_day_high`/`signal_n_day_low`（60 日收盤新高/新低）、`signal_boll_breakout_upper`/`signal_boll_breakdown_lower`、`signal_volume_surge`（量比 ≥ 2）、`signal_limit_up`/`signal_limit_down`、`signal_limit_down_recovery`（跌停翻紅）、`signal_broken_limit_up`（漲停打開）。漲跌停優先用 `consecutive_limit_ups >= 1`。

## 不可直接引用的資料

下列資料不在 enriched DataFrame 中：三大法人買賣超、融資融券、借券、月營收與財報、產業/概念分類、盤中分時價、五檔委買委賣。
