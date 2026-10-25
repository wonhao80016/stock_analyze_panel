# 策略开发指南

本文档是策略开发的完整参考。人类开发者参考它编写策略。

## 1. 策略文件格式

每个策略是一个 Python 文件，放在以下目录：

- 内置策略: `backend/app/strategy/builtin/`
- 自定义策略: `data/strategies/custom/`，建议文件名和 ID 使用 `custom_时间戳`
- AI 生成策略: `data/strategies/ai/`，文件名和 ID 使用 `ai_时间戳`

> ⚠️ **铁律**：AI/自定义生成的策略**只能**放入 `data/strategies/ai/` 或 `data/strategies/custom/`。严禁放入 `backend/app/strategy/builtin/`（内置策略目录，仅项目维护者可改），严禁借策略定制功能创建多个文件或修改任何项目源代码。

手动放入目录的策略文件不要与 Python 模块同名（如 `json.py`）：引擎以文件名作为模块名加载，同名文件会在整个进程中替换该模块。通过界面或 API 保存的 `custom_`、`ai_` ID 不会遇到这个问题。

## 2. 文件结构模板

```python
"""策略简短描述"""
import polars as pl

META = {
    "id": "strategy_id",              # 英文ID, 唯一, 文件名同名；自定义策略建议 custom_时间戳
    "name": "策略中文名",              # 显示名称
    "description": "策略详细描述",     # 一句话说明策略逻辑
    "tags": ["标签1", "标签2"],        # 分类标签

    # 基础过滤参数 (Stage 1, 引擎统一处理)
    "basic_filter": {
        "price_min": 5,               # 最低价格
        "price_max": 200,             # 最高价格
        "amount_min": 1e8,            # 最小成交额 (元)
        "exclude_st": False,          # 按名称排除 ST/*ST/退市 (A 股惯例, 台股无效, 引擎默认 False)
        "exclude_new_days": 30,       # 排除上市未满 N 个交易日的新股 (0 关闭)
    },

    # 策略参数 (只把用户可能调节的阈值放这里，公式常数不必参数化)
    # type 支持: float / int / bool / select(带 options) / date(格式 "YYYY-MM-DD")
    # float/int 可带 min/max/step；select 带 options: [{label, value}]；date 的 default 是字符串
    "params": [
    ],

    # 评分权重 (用于排序, 根据策略核心逻辑定制, 权重总和 = 1.0)
    "scoring": {
    },

    "order_by": "score",              # 排序字段, 通常用 "score"
    "descending": True,               # True = 从高到低
    "limit": 100,                      # 最多返回条数
}

# 买入信号 (回测 + 监控用, 根据策略逻辑选择合适的信号列)
ENTRY_SIGNALS = []

# 卖出信号
EXIT_SIGNALS = []

# 止损 (负数, 根据策略类型合理设定, 如做多短线 -0.05~-0.08)
STOP_LOSS = -0.05

# 最长持有天数 (短线 5~20, 中线 20~60)
MAX_HOLD_DAYS = 20

# 策略规则（人类可读，逐条编号，至少 3 条）
RULES = """
1. 规则描述一
2. 规则描述二
3. 规则描述三
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """策略核心过滤逻辑。

    df:     Stage 1 基础过滤后的 enriched 数据
    params: META.params 中定义的参数值 (用户可在前端覆盖)

    返回:   Polars 布尔表达式 (pl.Expr)
    """
    # 用 params.get("param_id", 默认值) 读取参数
    return (
        (pl.col("close") > pl.col("ma5"))
        & (pl.col("rsi_14") < 30)
    )
```

### 基础过滤（basic_filter）

- 未写的键沿用引擎默认值：`price_min` 3、`amount_min` 2000 万元、`boards` 为 TWSE/TPEX、`exclude_new_days` 30。
- `exclude_new_days` 以交易日计：从上市日或该代码在任一交易所最早的 K 线起算（上柜转上市不算新股），0 表示关闭。
- `*_min`/`*_max` 上下限会排除该字段缺值的行；数据中根本没有该字段时直接报错（市值类上下限除外，见下一条）。例如 `turnover_min` 会排除没有换手率的 ETF、TDR，以及换手率开始保存之前的交易日。
- `market_cap_*`、`float_cap_*` 依赖 `total_shares`/`float_shares`。台股数据没有这两列，这类上下限在选股和回测中都会被忽略，不要用它们筛选台股。
- 流动性门槛用成交额（`amount_min`），不要用固定股数；台股内置策略（如 `pullback_resume`、`swing_trend_confirmation`）默认成交额 ≥ 1 亿元。

### 历史窗口策略（filter_history）

普通 `filter()` 只接收当前日期的单日数据。当策略需要以下逻辑时，必须使用 `filter_history()`：

- "最近 N 天内出现过某个事件"（如涨停、金叉）
- "某个事件发生后的第 X 天"（如涨停后放量下跌）
- "前高 / 前低 / 上次某事件的价格"等需要回溯历史的自定义字段
- 任何需要多日数据才能计算的时序逻辑

**不需要** `filter_history()` 的场景：只用当日指标列做比较（如 close > ma60、rsi_14 < 30）。

策略必须显式声明唯一执行后端：普通表达式使用 `EXECUTION_BACKEND = "polars_expr"`，历史窗口使用 `EXECUTION_BACKEND = "python_history_legacy"`；不要同时定义 `filter()` 和 `filter_history()`。`META` 同时声明 `asset_types` 与 `timeframes`。

```python
LOOKBACK_DAYS = 8  # 回看交易日数，根据策略需要设置

def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """选股时 df 只包含目标日期及其之前 LOOKBACK_DAYS 个交易日的数据（共 LOOKBACK_DAYS + 1 个交易日，所有股票混合）；
    回测时 df 是整个回测区间（含暖机）的数据。
    每行包含 symbol, date 及所有指标列/信号列。

    返回值: 筛选后的 DataFrame。
    重要: 返回所有匹配的行，不要只过滤最新日期，否则回测只有最后一天有信号。
    """
    if df.is_empty() or "date" not in df.columns:
        return df

    down_pct = float(params.get("prev_down_pct", -0.02))
    vol_ratio = float(params.get("volume_ratio", 1.2))
    tolerance = float(params.get("reversal_tolerance", 0.005))

    # 示例: 前日明显阴线下跌，今日放量阳线反包前日实体
    hist = (
        df.sort(["symbol", "date"])
        .with_columns([
            pl.col("open").shift(1).over("symbol").alias("_prev_open"),
            pl.col("high").shift(1).over("symbol").alias("_prev_high"),
            pl.col("close").shift(1).over("symbol").alias("_prev_close"),
            pl.col("volume").shift(1).over("symbol").alias("_prev_volume"),
            pl.col("change_pct").shift(1).over("symbol").alias("_prev_change_pct"),
        ])
    )

    return hist.filter(
        (pl.col("_prev_close") < pl.col("_prev_open"))
        & (pl.col("_prev_change_pct") <= down_pct)
        & (pl.col("close") > pl.col("open"))
        & (pl.col("close") > pl.col("_prev_open"))
        & (pl.col("close") >= pl.col("_prev_high") * (1 - tolerance))
        & (pl.col("volume") >= pl.col("_prev_volume") * vol_ratio)
        & ((pl.col("close") > pl.col("ma5")) | (pl.col("close") > pl.col("ma10")))
    )
```

**关键要点：**
- `LOOKBACK_DAYS` 决定选股时 `filter_history()` 收到多少天的数据（目标日加之前 LOOKBACK_DAYS 个交易日；参数 `lookback_days` 更大时以参数为准），单独运行与批量运行相同；设为策略逻辑需要的最大回看天数
- 优先使用 Polars 的 `with_columns`、`over("symbol")`、`group_by`、`join`、`filter` 实现历史逻辑，避免把数据转成 Python list/dict 循环
- 只有遇到表达式难以描述的复杂状态机时，才使用 `partition_by("symbol")` + `to_dicts()` 逐股票分析
- **返回所有匹配行，不要过滤 `latest`**；选股引擎会自动取最新日，回测引擎需要全区间命中
- 回测时 `filter_history()` 收到整个回测区间的数据：只能用按 `symbol` 分组的 `shift`/`rolling` 等时序运算，不要用跨日期的整表汇总（如 `df["date"].max()`、不分日期的 rank/mean/quantile），否则回测与选股的结果会不一致
- 未声明 `filter_history()` 的策略走普通 `filter()` 路径，不受影响
- **date 类型参数必须先转换再与 `date` 列比较**：params 里的 `"type": "date"` 参数从 JSON 传来是字符串（如 `"2024-01-01"`），而数据中 `date` 列是 Polars Date 类型，**不能直接比较**，否则报错。必须先转换：

```python
from datetime import date as _date
anchor_raw = params.get("anchor_date", "2024-01-01")
anchor_date = _date.fromisoformat(anchor_raw) if isinstance(anchor_raw, str) else anchor_raw
# 之后才能: pl.col("date") == anchor_date  或  pl.col("date") > anchor_date
```

### 矩阵策略（matrix_native）

矩阵策略声明 `EXECUTION_BACKEND = "matrix_native"`，用 `MATRIX_STRATEGY` 对象提供 `required_fields()`、`required_warmup_bars(params)` 与 `compute_signals(market, params)`，只能导入 NumPy 与 `app.backtest.matrix`。

- 暖机：`compute_signals` 读取 `ema*`、`macd_*`、`rsi_*`、`atr_*`、`kdj_*` 等递归指标时，`required_warmup_bars` 至少取 `recursive_warmup_bars(字段)`（可从 `app.backtest.matrix` 导入；例如 ema60 为 337、rsi_14 为 140、macd_dea 为 188）。这些指标只有起始的种子期为空，之后的数值在暖机根数内仍受数据起点影响。
- 窗口函数：K 线有缺口时用 `valid_rolling_*`（`min`/`max`/`mean`/`sum`/`std`/`quantile`，参数为数值矩阵、有效 K 线遮罩如 `np.isfinite(market.close)`、窗口，`quantile` 另加分位数）；原始 `rolling_*` 只要窗内缺一根就整窗为 NaN。
- 可选方法 `required_coverage_fields(self, params) -> frozenset[str]`：返回入场判断必需的字段，由框架统一检查：
  - 选股（含监控）检查目标交易日，只计算股票池与策略资产范围内的标的；回测检查每个正式交易日。
  - 合格标的指当日有收盘价、且可能有该字段值的标的；`turnover_rate` 只计有换手率分母的股票（ETF、TDR 不算）。
  - 某交易日合格标的中有值的比例低于 50%（`MIN_SESSION_COVERAGE`）时拒绝执行；范围内没有任何合格标的时也直接拒绝。提示信息用字段显示名（`app/strategy/field_labels.py`，没有显示名的字段直接显示 ID）。
  - 回测整体通过、但某一交易所在部分交易日低于 50% 时，结果附带 `exchange_coverage_warnings`（字段、交易所、最多列出 5 个交易日与总数），不拒绝。选股端没有逐交易所的警告。
- 策略自己需要覆盖率时，用 `app.backtest.matrix` 的 `session_coverage(market, 字段, rows)` 与 `MIN_SESSION_COVERAGE`：合格标的定义与 50% 门槛和框架相同，但不套用股票池与资产范围。
- 可选方法 `limit_entries(self, entry, score, params)`：在框架的基础过滤与资产范围之后调用，收到入场矩阵的副本和策略自己的 `score`，返回与市场同形的布尔遮罩；结果与原入场取 AND，只能删减、不能新增入场，形状不符时报错。典型用途是每个交易日只保留前 N 名。

## 3. 常用指标列（参考，可直接使用）

以下列可直接引用。使用 `filter_history()` 或其他无法静态解析的 Python 逻辑时，必须声明最终依赖的公开字段，基础 OHLCV 和指标中间列不需要声明：

```python
REQUIRED_FEATURES = {"ma20", "momentum_20d", "vol_ratio_5d"}
```

未声明时回测会明确告警并暂时回退到全量特征计算。

### 通用列

| 列名 | 类型 | 说明 |
|------|------|------|
| symbol | string | 股票代码 (如 2330.TWSE、6488.TPEX) |
| date | date | 交易日期 |

### 价格相关

| 列名 | 类型 | 说明 |
|------|------|------|
| open, high, low, close | float | OHLCV 开高低收 (前复权) |
| raw_close, raw_high, raw_low | float | 原始未复权价 |
| prev_close | float | 昨收价 |
| change_pct | float | 涨跌幅 (如 0.032 = +3.2%) |
| change_amount | float | 涨跌额 |
| amount | float | 成交额 |
| amplitude | float | 振幅 |

### 均线

| 列名 | 说明 |
|------|------|
| ma5, ma10, ma20, ma30, ma60 | 简单移动均线 |
| ema5, ema10, ema20, ema30, ema60 | 指数移动均线 |

### 技术指标

| 列名 | 说明 |
|------|------|
| macd_dif | MACD DIF 线 |
| macd_dea | MACD DEA 线 |
| macd_hist | MACD 柱状，DIF − DEA（不乘 2） |
| boll_upper, boll_lower | 布林带上/下轨，MA20 ± 2 倍总体标准差 |
| kdj_k, kdj_d, kdj_j | KDJ 指标 |
| rsi_6, rsi_14, rsi_24 | RSI 相对强弱 |
| atr_14 | 平均真实波幅 |

EMA、MACD、RSI、ATR 在每只股票最初的若干根 K 线为空：EMA 以前 n 根的 SMA 起始，RSI、ATR 用 Wilder 平滑；KD 从 50 起算，9 日最高等于最低时 K 维持前值；RSI 无涨无跌时为 50，只涨不跌为 100。

### 量能

| 列名 | 说明 |
|------|------|
| volume | 成交量 |
| vol_ma5, vol_ma10 | 成交量均线 |
| vol_ratio_5d | 5日量比 |
| turnover_rate | 换手率，百分数（2 表示 2%）。台股分母是已发行普通股扣除私募股，不是流通股；每个交易日只在自己的盘后发布时计算并保留，不回推，之前的交易日为空；ETF、TDR 为空 |

### 动量与波动

| 列名 | 说明 |
|------|------|
| momentum_5d / 10d / 20d / 30d / 60d | N日涨幅 |
| annual_vol_20d | 20日年化波动率：20 个日对数收益的样本标准差 × √252 |
| high_60d, low_60d | 含当日的 60 日收盘最高/最低价（不是盘中最高/最低价） |

### 受控虚拟评分字段

| 列名 | 说明 |
|------|------|
| ma20_bias | `close / ma20 - 1`，仅在内存中计算，不写入数据表 |

### 涨跌停

| 列名 | 说明 |
|------|------|
| consecutive_limit_ups | 连续涨停天数 |
| consecutive_limit_downs | 连续跌停天数 |

### 运行时附加列（由引擎从 instruments 表 JOIN）

| 列名 | 说明 |
|------|------|
| name | 股票名称 |
| total_shares | 总股本 |
| float_shares | 流通股本 |

（`total_shares` 和 `float_shares` 用于 `basic_filter` 中计算市值：`close * total_shares`）

台股：`total_shares`、`float_shares` 没有数据，市值上下限会被忽略；Polars 策略回测时 `name` 取自证券主档，选股时 instruments 表为空则没有 `name` 列。

## 4. 常用信号列（参考）

信号列是布尔值，**必须**使用 `.fill_null(False)` 处理空值。同样仅供参考，根据策略含义自行选择匹配的。

| 列名 | 方向 | 说明 |
|------|------|------|
| signal_ma_golden_5_20 | 买入 | MA5 上穿 MA20 |
| signal_ma_dead_5_20 | 卖出 | MA5 下穿 MA20 |
| signal_ma_golden_20_60 | 买入 | MA20 上穿 MA60 |
| signal_macd_golden | 买入 | MACD 金叉 |
| signal_macd_dead | 卖出 | MACD 死叉 |
| signal_ma20_breakout | 买入 | 突破 MA20 |
| signal_ma20_breakdown | 卖出 | 跌破 MA20 |
| signal_n_day_high | 买入 | 收盘达 60 日收盘最高 |
| signal_n_day_low | 卖出 | 收盘达 60 日收盘最低 |
| signal_boll_breakout_upper | 中性 | 突破布林上轨 |
| signal_boll_breakdown_lower | 中性 | 跌破布林下轨 |
| signal_volume_surge | 中性 | 放量 |
| signal_limit_up | 买入 | 涨停 (见下方注意) |
| signal_limit_down | 卖出 | 跌停 (见下方注意) |
| signal_limit_down_recovery | 买入 | 跌停翻红：盘中触及跌停价、收盘高于昨收 (见下方注意) |
| signal_broken_limit_up | 卖出 | 炸板 (见下方注意) |

> **注意**：台股已发布的数据带有这四个涨跌停信号，Polars 策略回测时缺少的行按台股发布规则补算，不依赖 instruments 表；其他市场仍需要 instruments 表（板块代码）才能计算，部分环境不生成。如果策略只用涨停判断，优先用 `consecutive_limit_ups >= 1`（稳定列，始终可用）。

此外，用户自定义信号（`data/user_data/custom_signals/`）以 `csg_` 前缀注入，也可在 filter() 中引用。台股盘中行情不提供成交额，自定义信号计算时当日行的 `amount` 为空。

## 5. 不可用的数据（重要）

以下数据**不在** enriched DataFrame 中，策略代码中**不能**直接引用：

| 数据 | 说明 |
|------|------|
| 财务数据 (PE/PB/ROE/净利润/营收/资产负债等) | 存储在独立 financials 表，未 JOIN |
| 扩展数据 (概念/行业/人气排名/资金流向等) | 存储在 ext_data 目录，未 JOIN |
| 盘中实时数据 (分时价/五档盘口等) | 仅前端轮询使用 |

如需财务或扩展数据作为筛选条件，需先在系统层面完成 JOIN 再提供给策略（当前未实现）。

## 6. 规则

1. `filter()` 必须返回 `pl.Expr` (用 `&` `|` 组合布尔表达式)；`filter_history()` 返回筛选后的 `DataFrame`
2. 信号列使用 `.fill_null(False)` 处理空值
3. 用户可能调节的数值阈值通过 `params` 暴露；公式常数、固定窗口边界、一次性内部变量不必强行参数化
4. `scoring` 只能使用上述真实数值字段或受控虚拟字段 `ma20_bias`，权重总和必须为 1.0；不要使用条件名称或信号列作为评分字段。某字段要低值得高分时，在 META 写 `"scoring_directions": {"字段": "low"}`（默认 `"high"`，只接受这两个值）；不要用 `descending=False`：它把整个分数倒序排列（分数最低者排最前），引用这种策略的叠加策略无法加载，保存的子策略设置里含它时运行会报错
5. 回测默认为台股现股模式：当日买入次日才能卖出；只有当冲模式且标的可当冲时才允许当日卖出
6. Polars 策略允许 `import polars as pl` 和 `from datetime import date/datetime`；矩阵策略只允许 NumPy 和 `app.backtest.matrix` 协议/算子
7. 禁止使用 `open()`, `exec()`, `eval()`, `os`, `sys`, `subprocess`
8. **贴合用户需求优先**：第3/4节的指标列和信号列仅供参考，能用则用；如果用户需求需要自定义计算（如"前高""上次涨停价""N日内某个事件后X天"），直接在 `filter_history()` 中自行设计和计算，不需要局限于已有列
9. `filter_history()` 中优先用 Polars 向量化语法；仅在复杂状态机无法清晰表达时，才用 `partition_by("symbol")` 逐股票分析
10. 评分缺值：候选缺任一评分字段的值时分数为 null，结果保留、排在最后，看板分级为「观察」；0 分是合法的最低分。评分字段在数据中不存在时直接报错。回测时某评分字段在候选中的覆盖率低于 50% 会拒绝执行

## 7. 策略示例

### 强势反包

```python
"""强势反包 — 前日阴线下跌 + 今日放量阳线反包"""
import polars as pl

META = {
    "id": "strong_reversal",
    "name": "强势反包",
    "description": "前一日明显阴线下跌，今日放量阳线收复前一日阴线实体",
    "tags": ["反包", "短线", "放量"],
    "basic_filter": {
        "price_min": 3, "price_max": 200, "amount_min": 0.5e8,
        "exclude_new_days": 30,
    },
    "params": [
        {"id": "prev_down_pct", "label": "前日最大跌幅", "type": "float",
         "default": -0.02, "min": -0.10, "max": -0.005, "step": 0.005},
        {"id": "volume_ratio", "label": "成交量放大倍数", "type": "float",
         "default": 1.2, "min": 1.0, "max": 5.0, "step": 0.1},
        {"id": "reversal_tolerance", "label": "反包容忍误差", "type": "float",
         "default": 0.005, "min": 0.0, "max": 0.03, "step": 0.005},
    ],
    "scoring": {"change_pct": 0.4, "vol_ratio_5d": 0.3, "momentum_5d": 0.3},
    "order_by": "score", "descending": True, "limit": 100,
}

LOOKBACK_DAYS = 2

ENTRY_SIGNALS = ["signal_volume_surge"]
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 10

RULES = """
1. 前一交易日为阴线，且跌幅不小于设定阈值
2. 今日为阳线，收盘价收复前一日开盘价并接近或突破前一日高点
3. 今日成交量较前一日明显放大，且收盘价站上 MA5 或 MA10
"""

def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    if df.is_empty() or "date" not in df.columns:
        return df

    down_pct = float(params.get("prev_down_pct", -0.02))
    vol_ratio = float(params.get("volume_ratio", 1.2))
    tolerance = float(params.get("reversal_tolerance", 0.005))
    hist = (
        df.sort(["symbol", "date"])
        .with_columns([
            pl.col("open").shift(1).over("symbol").alias("_prev_open"),
            pl.col("high").shift(1).over("symbol").alias("_prev_high"),
            pl.col("close").shift(1).over("symbol").alias("_prev_close"),
            pl.col("volume").shift(1).over("symbol").alias("_prev_volume"),
            pl.col("change_pct").shift(1).over("symbol").alias("_prev_change_pct"),
        ])
    )
    return hist.filter(
        (pl.col("_prev_close") < pl.col("_prev_open"))
        & (pl.col("_prev_change_pct") <= down_pct)
        & (pl.col("close") > pl.col("open"))
        & (pl.col("close") > pl.col("_prev_open"))
        & (pl.col("close") >= pl.col("_prev_high") * (1 - tolerance))
        & (pl.col("volume") >= pl.col("_prev_volume") * vol_ratio)
        & ((pl.col("close") > pl.col("ma5")) | (pl.col("close") > pl.col("ma10")))
    )
```
