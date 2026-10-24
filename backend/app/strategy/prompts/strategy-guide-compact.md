# AI 策略生成精简指南

只生成一个完整 Python 策略文件，直接输出代码，不要输出 Markdown 解释。

## 必须遵守

1. Polars/历史策略限用 `polars/datetime`；矩阵策略限用 `numpy/app.backtest.matrix`。
2. AI 策略位于 `data/strategies/ai/`，`META.id` 用指定的 `ai_` ID。
3. 禁止文件读写和 `open/exec/eval/compile/__import__/globals/locals/vars/dir/getattr/setattr/delattr/type/input`。
4. `META.params` 只放可调项：必填 `id/label/type/default`；数值项加 `min/max/step`，select 加 `options`。
5. `META.scoring` 只用真实数值字段或 `ma20_bias`，权重和为 1.0；字段低值优先用 `"scoring_directions": {"字段": "low"}`，勿用 `descending=False`。缺评分值的候选分数为 null、排最后；0 分有效。
6. `ENTRY_SIGNALS/EXIT_SIGNALS` 只选相关信号，无匹配项可为空。
7. `RULES` 用中文列出至少 3 条核心逻辑。
8. 优先 Polars 向量化，避免逐行循环。
9. `META` 须为顶层字面量字典（可用 `META: dict = {...}`），禁止改名或动态构造。

## 文件结构

```python
"""策略简短描述"""
import polars as pl

META = {
    "id": "ai_xxxxxxxxxxxx",
    "name": "策略中文名",
    "description": "一句话说明策略逻辑",
    "tags": ["标签"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "amount_min": 1e8,  # 成交额(元)
        "exclude_new_days": 30,  # 交易日
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
1. 规则一
2. 规则二
3. 规则三
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
```

## 何时使用 filter_history

普通 `filter()` 只判断当日数据。需要多日数据（近 N 天出现某事件、涨停后第 X 天、前高前低、连续阴跌等）时必须用 `filter_history(df, params) -> pl.DataFrame`：

```python
LOOKBACK_DAYS = 8
EXECUTION_BACKEND = "python_history_legacy"
REQUIRED_FEATURES = {"ma20", "momentum_20d"}  # 必须声明读取的公开字段

def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    if df.is_empty() or "date" not in df.columns:
        return df
    hist = df.sort(["symbol", "date"]).with_columns([
        pl.col("close").shift(1).over("symbol").alias("_prev_close"),
    ])
    return hist.filter(pl.col("close") > pl.col("_prev_close"))
```

选股时 df 是目标日及前 `LOOKBACK_DAYS` 个交易日，回测时是整段数据：只用按 `symbol` 的 shift/rolling，不要 `df["date"].max()` 或不分日期的 rank/mean；返回所有匹配行，不要只留最新日。

## matrix_native 文件结构

请求指定 `matrix_native` 时，不得生成 `filter()` 或 `filter_history()`：

```python
import numpy as np
from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

META = {"id": "custom_matrix_example", "name": "矩阵示例", "description": "...",
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

- 读 `ema*/macd_*/rsi_*/atr_*/kdj_*` 时暖机至少 `recursive_warmup_bars("字段")`（ema60 为 337），起始几根为空。
- 缺 K 用 `valid_rolling_*(值, np.isfinite(market.close), 窗口)`，`rolling_*` 窗内缺一根即 NaN。
- 可选 `required_coverage_fields(self, params)`：返回入场必需字段；某交易日合格标的有值比例低于 `MIN_SESSION_COVERAGE`（50%）即拒绝（回测逐日检查），自查用 `session_coverage(market, "字段")`。
- 可选 `limit_entries(self, entry, score, params)`：基础过滤后调用，返回同形布尔遮罩，只能删减入场。

date 参数是字符串，比较前先转换：`_date.fromisoformat(raw) if isinstance(raw, str) else raw`（`from datetime import date as _date`）。

## 常用字段

通用：`symbol`, `date`, `name`
价格：`open`, `high`, `low`, `close`, `raw_close`, `raw_high`, `raw_low`, `prev_close`, `change_pct`, `change_amount`, `amount`, `amplitude`
均线：`ma5`, `ma10`, `ma20`, `ma30`, `ma60`, `ema5`, `ema10`, `ema20`, `ema30`, `ema60`
技术指标：`macd_dif`, `macd_dea`, `macd_hist`, `boll_upper`, `boll_lower`, `kdj_k`, `kdj_d`, `kdj_j`, `rsi_6`, `rsi_14`, `rsi_24`, `atr_14`（hist=DIF−DEA）
量能：`volume`, `vol_ma5`, `vol_ma10`, `vol_ratio_5d`, `turnover_rate`（%，分母为已发行普通股扣私募；各交易日发布后才有，ETF/TDR 为空）
动量与波动：`momentum_5d`, `momentum_10d`, `momentum_20d`, `momentum_30d`, `momentum_60d`, `annual_vol_20d`, `high_60d`, `low_60d`
虚拟评分：`ma20_bias = close / ma20 - 1`。
涨跌停：`consecutive_limit_ups`, `consecutive_limit_downs`
台股无 `total_shares`/`float_shares`，市值上下限被忽略。

## 常用信号列

信号列是布尔值，用时加 `.fill_null(False)`：`signal_ma_golden_5_20`/`signal_ma_dead_5_20`（MA5 上/下穿 MA20）、`signal_ma_golden_20_60`（MA20 上穿 MA60）、`signal_macd_golden`/`signal_macd_dead`（MACD 金/死叉）、`signal_ma20_breakout`/`signal_ma20_breakdown`、`signal_n_day_high`/`signal_n_day_low`（60 日收盘新高/新低）、`signal_boll_breakout_upper`/`signal_boll_breakdown_lower`、`signal_volume_surge`、`signal_limit_up`/`signal_limit_down`、`signal_limit_down_recovery`（跌停翘板）、`signal_broken_limit_up`（炸板）。涨跌停优先用稳定列 `consecutive_limit_ups >= 1`。

## 不可直接引用的数据

以下数据不在 enriched DataFrame 中，不能直接引用：财务数据、扩展数据、概念/行业/人气排名/资金流向、盘中分时价、五档盘口。
