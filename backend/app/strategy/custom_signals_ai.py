"""AI 生成自定义信号 — 组装提示词 + 解析并校验 AI 返回的结构化条件。

职责:
  - build_messages(description): 把用户一句描述 + 字段白名单/运算符/格式要求组装成 LLM 消息
  - parse_and_validate(text): 把 LLM 返回的 JSON 解析为 {name, conditions}，并复用
    custom_signals.validate() 做白名单/运算符/偏移校验（安全闸门）

不知道: API、AI 调用、持久化。纯函数，无副作用。
"""
from __future__ import annotations

import json
import re

from app.indicators.pipeline import ENRICHED_COLUMNS_BY_CATEGORY
from app.strategy import custom_signals
from app.strategy.field_labels import field_label

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _describe(field: str) -> str:
    unit = custom_signals.UNIT_LABELS[custom_signals.FIELD_UNITS[field]]
    return f"{field}({field_label(field)}; 單位: {unit})"


def _format_fields() -> str:
    """Whitelisted fields by category as key(label; unit), for the LLM."""
    allowed = custom_signals.ALLOWED_FIELDS
    lines: list[str] = []
    quote = sorted(f for f in custom_signals.QUOTE_FIELDS if f in allowed)
    lines.append("行情: " + ", ".join(_describe(f) for f in quote))
    listed = set(quote)
    for cat, label in custom_signals.FIELD_GROUP_LABELS.items():
        fields = [f for f in ENRICHED_COLUMNS_BY_CATEGORY.get(cat, []) if f in allowed]
        if fields:
            lines.append(f"{label}: " + ", ".join(_describe(f) for f in fields))
            listed.update(fields)
    rest = sorted(allowed - listed)
    if rest:
        lines.append("漲跌停: " + ", ".join(_describe(f) for f in rest))
    return "\n".join(lines)


_SYSTEM_TEMPLATE = """你是台股量化訊號設計專家。使用者會描述一個訊號思路, 你要把它拆解為布林條件組合 (多條件之間是「且」關係, 即同時滿足), 並輸出結構化 JSON 供系統編譯為選股/回測/監控訊號。

可用欄位 (白名單, 只能使用以下欄位, 禁止自創或使用白名單之外的欄位):
{fields}

單位說明 (常數門檻必須依欄位單位填寫):
- 小數: change_pct、amplitude、momentum_*、annual_vol_20d、distance_to_* 都是小數, 0.05 表示 5%
- 百分數: turnover_rate 是百分數, 5 表示 5%
- 成交量 volume、vol_ma5、vol_ma10 以股計, 1 張 = 1000 股; 成交額 amount 與價格欄位以元計

運算子 (op): >  >=  <  <=  ==  !=

右值 (right):
- 數字: 寫字串形式, 如 "2"、"3000"、"0.05"
- 另一欄位: 必須帶 "field:" 前綴, 如 "field:ma20"; 嚴禁裸寫欄位名, 如 "macd_dea" 應寫成 "field:macd_dea"

日期偏移 (leftDays / rightDays): 取 N 個交易日前的值, 0 = 當日最新; 範圍 0~{max_days}。只有明確需要「前N日」時才使用偏移。

要求:
1. 只輸出一個 JSON 物件, 禁止 markdown 程式碼區塊、禁止任何解釋或多餘文字。
2. JSON 結構固定為:
{{"name": "簡短繁體中文訊號名稱(≤12字)", "conditions": [
  {{"left": "欄位", "op": "運算子", "right": "數字字串或field:欄位", "leftDays": 0, "rightDays": 0}}
]}}
   範例 (右值引用另一欄位時必須帶 field: 前綴, 不能裸寫欄位名):
   {{"name": "MACD黃金交叉", "conditions": [
     {{"left": "macd_dif", "op": ">", "right": "field:macd_dea", "leftDays": 0, "rightDays": 0}}
   ]}}
3. conditions 至少 1 個、最多 8 個; 優先用最少的條件表達清楚的思路。
4. 多條件必須能同時滿足, 不要輸出互相矛盾的條件。
5. name 一律使用繁體中文, 不得使用簡體字。"""


def build_messages(description: str) -> list[dict]:
    """组装 LLM 消息：[system 提示词, user 描述]。"""
    system = _SYSTEM_TEMPLATE.format(
        fields=_format_fields(),
        max_days=custom_signals.MAX_DAYS,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": description},
    ]


def parse_and_validate(text: str) -> dict:
    """解析并校验 AI 返回的 JSON → {"name", "conditions"}。非法时抛 ValueError。

    只取 name 与 conditions；id / kind 由用户在表单里填写，不信任 AI。
    """
    raw = _extract_json_object(text)
    if not isinstance(raw, dict):
        raise ValueError("AI 返回的 JSON 不是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("AI 未返回信号名称 name")
    name = name.strip()[:30]
    conditions_raw = raw.get("conditions")
    if not isinstance(conditions_raw, list) or not conditions_raw:
        raise ValueError("AI 未返回任何条件 conditions")

    conditions = [_normalize_condition(c) for c in conditions_raw]

    # 复用现有白名单/运算符/偏移校验作为安全闸门（id/kind 用占位值）。
    probe = {
        "id": "aigenerated",
        "name": name,
        "kind": "both",
        "conditions": conditions,
    }
    custom_signals.validate(probe)
    return {"name": name, "conditions": conditions}


def _normalize_condition(c: object) -> dict:
    if not isinstance(c, dict):
        raise ValueError("条件的每一项必须是 JSON 对象")
    left = c.get("left")
    op = c.get("op")
    right = c.get("right")
    if right is None:
        raise ValueError("条件缺少右值 right")
    if isinstance(right, bool):
        raise ValueError("右值不能是布尔值")
    if isinstance(right, (int, float)):
        right = _num_to_str(right)
    if not isinstance(right, str) or not right.strip():
        raise ValueError(f"右值非法: {right!r}")
    right = right.strip()
    # 兜底: AI 偶尔漏写 field: 前缀的裸字段名, 补全为规范形式
    if not right.startswith("field:") and right in custom_signals.ALLOWED_FIELDS:
        right = f"field:{right}"
    return {
        "left": str(left),
        "op": str(op),
        "right": right,
        "leftDays": _norm_days(c.get("leftDays")),
        "rightDays": _norm_days(c.get("rightDays")),
    }


def _norm_days(value: object) -> object:
    """归一化日期偏移为 int（缺省 0）；无法转换时原样返回，由 validate 报中文错误。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _num_to_str(value) -> str:
    if isinstance(value, int):
        return str(value)
    f = float(value)
    return str(int(f)) if f.is_integer() else str(f)


def _extract_json_object(text: str) -> object:
    """从 LLM 文本提取 JSON 对象（多级容错）。

    依次尝试: 整段 → markdown 围栏内 → 首个 {...} 平衡块;
    每级再对 尾随垃圾 / 尾逗号 做轻量修复。全部失败才报错。
    """
    source = text or ""
    candidates: list[str] = []
    stripped = source.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(
        match.group(1).strip() for match in _FENCED_JSON_RE.finditer(source)
    )
    brace = _first_brace_block(source)
    if brace and brace.strip() not in candidates:
        candidates.append(brace)
    last_error: Exception | None = None
    for candidate in candidates:
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        try:
            json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
    raise ValueError(f"AI 返回的不是合法 JSON: {last_error}")


def _try_parse_json(candidate: str) -> object | None:
    """尽力解析一段可能带尾随垃圾 / 尾逗号的 JSON；失败返 None。"""
    variants = [candidate.strip()]
    last = candidate.rfind("}")
    if last >= 0 and last < len(candidate) - 1:
        variants.append(candidate[:last + 1].strip())
    for v in variants:
        if not v:
            continue
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
        # 去掉数组/对象结尾的多余逗号 (AI 常见错误): `,}` / `,]`
        cleaned = re.sub(r",\s*([}\]])", r"\1", v)
        if cleaned != v:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    return None


def _first_brace_block(text: str) -> str:
    """括号配对截取首个 {...} 块（AI 偶尔混入前后解释文字时的兜底）。"""
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]
