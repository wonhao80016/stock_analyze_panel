"""用户偏好设置持久化。

存储位置: data/user_data/preferences.json
沿用 secrets_store 的 merge-write 模式,但不做 chmod 0600 (非敏感数据)。
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PREFERENCES_STORE_UNAVAILABLE_DETAIL = (
    "偏好设置文件(数据目录下的 user_data/preferences.json)已损坏或无法读取,本次设置未保存。"
    "请修复该文件的内容或权限后重试"
)


class PreferencesStoreError(RuntimeError):
    """preferences.json exists but cannot be used as the base of a write."""


class PreferencesCorruptError(PreferencesStoreError):
    """preferences.json is not valid JSON or not a JSON object."""


class PreferencesUnreadableError(PreferencesStoreError):
    """preferences.json exists but cannot be opened or read, e.g. permission denied."""


# 进程内缓存: 行情轮询线程一轮会调用 8~12 次 getter, 每次读盘+parse 是纯重复;
# 文件仅在用户改设置时变化, 以 (mtime_ns, size) 签名判断是否重读。
# A malformed file is remembered per signature too, so it is neither re-read nor re-logged.
# Unreadable files are not: a transient lock (e.g. antivirus) leaves the signature unchanged.
_cache_entry: tuple[tuple[int, int], dict] | None = None
_failure_entry: tuple[tuple[int, int], PreferencesCorruptError] | None = None
# Bumped by every save; a reader stores its result only if no save ran while it was reading,
# because the new file can carry the same (mtime_ns, size) signature as the one it read.
_version = 0
_state_lock = threading.Lock()
# Serializes every read-modify-write of preferences.json within this process.
_write_lock = threading.RLock()
_READ_ATTEMPTS = 5
_READ_RETRY_SECONDS = 0.01
_UNREADABLE_LOG_INTERVAL_SECONDS = 60.0
_unreadable_logged_at: dict[str, float] = {}


def _path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _invalidate_cache() -> None:
    global _cache_entry, _failure_entry, _version
    with _state_lock:
        _cache_entry = None
        _failure_entry = None
        _version += 1


def _read_text(p: Path) -> str:
    # Windows denies opening a file while os.replace swaps it in, so a reader racing a save retries briefly.
    for attempt in range(1, _READ_ATTEMPTS):
        try:
            return p.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(_READ_RETRY_SECONDS * attempt)
    return p.read_text(encoding="utf-8")


def _unreadable(p: Path, kind: str, e: OSError) -> PreferencesUnreadableError:
    now = time.monotonic()
    with _state_lock:
        last = _unreadable_logged_at.get(kind)
        should_log = last is None or now - last >= _UNREADABLE_LOG_INTERVAL_SECONDS
        if should_log:
            _unreadable_logged_at[kind] = now
    if should_log:
        logger.error("preferences.json at %s cannot be read: %s (%s)", p, kind, e)
    return PreferencesUnreadableError(str(e))


def _read_file(p: Path) -> dict | None:
    """Parse *p*; None when it does not exist. Raises a PreferencesStoreError subclass otherwise."""
    try:
        text = _read_text(p)
    except FileNotFoundError:
        return None
    except PermissionError as e:
        raise _unreadable(p, "permission denied", e) from e
    except OSError as e:
        raise _unreadable(p, "os error", e) from e
    try:
        data = json.loads(text)
    except ValueError as e:
        logger.error("preferences.json at %s is malformed: %s", p, e)
        raise PreferencesCorruptError(str(e)) from e
    if not isinstance(data, dict):
        logger.error("preferences.json at %s is malformed: not a JSON object", p)
        raise PreferencesCorruptError("not a JSON object")
    return data


def _load_cached() -> dict:
    global _cache_entry, _failure_entry
    p = _path()
    with _state_lock:
        version = _version
        entry = _cache_entry
        failure = _failure_entry
    try:
        st = p.stat()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise _unreadable(p, "stat failed", e) from e
    sig = (st.st_mtime_ns, st.st_size)
    if entry is not None and entry[0] == sig:
        return copy.deepcopy(entry[1])
    if failure is not None and failure[0] == sig:
        raise type(failure[1])(str(failure[1]))
    try:
        data = _read_file(p)
    except PreferencesCorruptError as e:
        with _state_lock:
            if _version == version:
                _failure_entry = (sig, e)
        raise
    if data is None:
        return {}
    with _state_lock:
        if _version == version:
            _cache_entry = (sig, data)
            _failure_entry = None
    return copy.deepcopy(data)


def load() -> dict:
    """读取 preferences.json (带 mtime 签名缓存)。返回深拷贝, 调用方可自由修改。

    An unreadable or corrupt file reads as {} so getters fall back to defaults; save() refuses it.
    """
    try:
        return _load_cached()
    except PreferencesStoreError:
        return {}


def _write(data: dict) -> None:
    from app.taiwan.universe.service import write_atomically

    text = json.dumps(data, indent=2, ensure_ascii=False)
    write_atomically(_path(), lambda tmp: tmp.write_text(text, encoding="utf-8"))
    _invalidate_cache()


def save(updates: dict) -> dict:
    """合并写入。返回新内容。

    Always re-reads the file rather than the cache. Raises a PreferencesStoreError subclass,
    leaving the file untouched, when the stored file cannot be read or parsed.
    """
    with _write_lock:
        current = _read_file(_path()) or {}
        current.update(updates)
        _write(current)
        return current


def get_realtime_quotes_enabled() -> bool:
    return load().get("realtime_quotes_enabled", False)


def get_taiwan_market_closed_date() -> str | None:
    """ISO date the user marked as an unscheduled Taiwan market closure; it only applies on that date."""
    value = load().get("taiwan_market_closed_date")
    return value if isinstance(value, str) else None


def set_taiwan_market_closed_date(day: str | None) -> None:
    save({"taiwan_market_closed_date": day})


def get_watchlist_groups_in_nav() -> bool:
    """自选分组是否显示在侧边栏（可展开二级子菜单）。默认 False。"""
    return load().get("watchlist_groups_in_nav", False)


def get_realtime_quote_interval() -> float:
    return load().get("realtime_quote_interval", 6.0)


def get_realtime_watchlist_symbols() -> list[str]:
    """Free 档自选实时监控标的:直接取自选页前 5 个。"""
    try:
        from app.services import watchlist
        rows = watchlist.list_symbols()
    except Exception as e:  # noqa: BLE001
        logger.warning("load watchlist for realtime failed: %s", e)
        return []
    out: list[str] = []
    for row in rows:
        symbol = str((row or {}).get("symbol") or "").strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
        if len(out) >= 5:
            break
    return out


def set_realtime_quote_interval(interval: float) -> float:
    """保存行情轮询间隔（不在此做 min/max 校验，由调用方按档位限制）。"""
    save({"realtime_quote_interval": interval})
    return interval


def get_minute_intraday_refresh() -> bool:
    """自选列表分时图是否跟随实时行情刷新。

    默认值随权限: 有实时行情权限 (Pro+) 的用户默认开启, 否则关闭。
    用户主动设置过的 (key 存在) 以用户选择为准, 即使是 False 也尊重。
    """
    data = load()
    if "minute_intraday_refresh" in data:
        return bool(data["minute_intraday_refresh"])
    # 未设置过: 有权限默认开, 无权限默认关。
    try:
        from app.services.quote_service import QuoteService
        return QuoteService.is_realtime_allowed()
    except Exception:
        return False


# 分时图实时刷新间隔允许范围 (秒)。下限 3s, 上限 60s。
_INTRADAY_REFRESH_INTERVAL_MIN = 3
_INTRADAY_REFRESH_INTERVAL_MAX = 60


def get_minute_intraday_refresh_interval() -> int:
    """分时图实时刷新轮询间隔 (秒)。默认 6s, 范围 [3, 60]。"""
    return max(_INTRADAY_REFRESH_INTERVAL_MIN,
               min(_INTRADAY_REFRESH_INTERVAL_MAX,
                   int(load().get("minute_intraday_refresh_interval", 6))))


# 监控中心个股通知 ext 字段默认配置。
# 无内置预设 (ext_gn_ths/ext_hy_ths 为 A 股专属, 不再自动建立), 默认留空,
# 由用户在设置页自行挑选已配置的维度。
_MONITOR_EXT_FIELDS_DEFAULT: dict = {}


def _normalize_ext_field(raw) -> dict | None:
    """规范化单个 ext 字段配置, 兼容旧字符串格式 ("id.field") 和新对象格式。

    新格式: {"field": "id.field", "maxTags": N, "hiddenIndices": [...]}
    maxTags=0 或缺省=不限制; hiddenIndices 指定要隐藏的位置 (0-based)。
    """
    if raw is None:
        return None
    # 旧格式: 纯字符串 "configId.fieldName"
    if isinstance(raw, str):
        return {"field": raw}
    if isinstance(raw, dict):
        field = raw.get("field")
        if not field:
            return None
        return {
            "field": field,
            "maxTags": int(raw["maxTags"]) if raw.get("maxTags") else 0,
            "hiddenIndices": [int(i) for i in raw["hiddenIndices"]] if raw.get("hiddenIndices") else [],
        }
    return None


def get_monitor_ext_fields() -> dict:
    """监控中心个股通知要展示的 ext 字段 (concept/industry)。

    返回 {"concept": {"field", "maxTags", "hiddenIndices"} | None, ...}。
    后端只需读 .field 构建 ext_columns; maxTags/hiddenIndices 供前端渲染裁剪。
    兼容旧字符串格式 ("id.field") 自动升级。
    """
    data = load()
    raw = data.get("monitor_ext_fields")
    if raw is None:
        return {
            "concept": _MONITOR_EXT_FIELDS_DEFAULT.get("concept"),
            "industry": _MONITOR_EXT_FIELDS_DEFAULT.get("industry"),
        }
    return {
        "concept": _normalize_ext_field(raw.get("concept")),
        "industry": _normalize_ext_field(raw.get("industry")),
    }


def get_minute_sync_days() -> int:
    return max(1, min(30, load().get("minute_sync_days", 5)))


# ===== 数据源选择 =====

# Stored "tickflow" values from older installs read back as "taiwan" in the getters below.
_ALLOWED_DATA_PROVIDERS = {"taiwan"}


def _allowed_data_providers() -> set[str]:
    try:
        from app.data_providers import custom as custom_sources
        return _ALLOWED_DATA_PROVIDERS | custom_sources.names()
    except Exception:  # noqa: BLE001
        return set(_ALLOWED_DATA_PROVIDERS)


def get_daily_data_provider() -> str:
    """Taiwan daily K always comes from the official source; a stored custom choice is ignored."""
    return "taiwan"


def get_adj_factor_provider() -> str:
    """Taiwan adjustment factors come from corporate_actions; a stored custom choice is ignored."""
    return "same_as_daily"


def get_minute_data_provider() -> str:
    provider = str(load().get("minute_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_realtime_data_provider() -> str:
    provider = str(load().get("realtime_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_financial_provider() -> str:
    provider = str(load().get("financial_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


# regime 全量回填分批参数范围:
# - batch_days: 每批目标交易日数。越小内存越省、批次越多越慢; ma20 需 20 交易日,
#   故下限 25(留 warmup 余量), 上限 500(约 2 年)。
# - warmup_days: 每批前缀预热天数(日历日), 必须 > ma20 的 20 交易日(≈28 日历日),
#   下限 35 留余量, 上限 90。
_REGIME_BATCH_DAYS_MIN = 25
_REGIME_BATCH_DAYS_MAX = 500
_REGIME_WARMUP_DAYS_MIN = 35
_REGIME_WARMUP_DAYS_MAX = 90


def get_regime_batch_days() -> int:
    """regime 全量回填每批目标交易日数。默认 60(约一季度)。

    超过此天数的范围会被切成多批, 每批独立算指标后拼接, 控制内存峰值。
    """
    v = load().get("regime_batch_days", 60)
    try:
        return max(_REGIME_BATCH_DAYS_MIN, min(_REGIME_BATCH_DAYS_MAX, int(v)))
    except (TypeError, ValueError):
        return 60


def get_regime_warmup_days() -> int:
    """regime 分批每批的 warmup 前缀日历天数。默认 40。

    用于预热 ma20 等滚动窗口指标, 使每批边界计算正确。必须 > 20 交易日。
    """
    v = load().get("regime_warmup_days", 40)
    try:
        return max(_REGIME_WARMUP_DAYS_MIN, min(_REGIME_WARMUP_DAYS_MAX, int(v)))
    except (TypeError, ValueError):
        return 40


def get_sentiment_exclude_st() -> bool:
    """市场环境/主线统计是否剔除风险警示(ST)股。默认 True。

    口径: 主板 ST 在 2026-07 前享 5% 涨跌幅(封板成本减半), 且 ST 是跨行业的
    状态桶而非投资题材, 混入会系统性抬高涨停宽度/高度(弱市尤甚)。剔除后
    涨跌家数等宽度占比几乎不受影响。修改后需重算 regime 与主线生效。
    """
    return bool(load().get("sentiment_exclude_st", True))


def get_enriched_batch_size() -> int:
    """返回 enriched 全量计算每批 symbol 数量。"""
    return max(1, min(10000, load().get("enriched_batch_size", 1000)))


# 监控规则可选的外部推播渠道。多选: 不推播 = 空数组, 而非 'none'。
REVIEW_PUSH_CHANNELS = {"line", "telegram"}


# ===== 实时监控 =====

# 页面 SSE 刷新配置: { "watchlist": true, "monitor": true, ... }
# 可刷新的页面列表及其默认值
SSE_REFRESH_PAGES_DEFAULT = {
    "watchlist": True,
}

# Mirrors app.taiwan.benchmarks.BENCHMARK_INDICES; kept literal because this module
# is imported on every hot getter path and must stay free of app.taiwan's package init.
SIDEBAR_INDEX_SYMBOLS_DEFAULT = ["t00.TWSE", "o00.TPEX"]


# ===== 盘中实时行情范围 (独立于盘后管道范围) =====


def get_realtime_pull_index() -> bool:
    return load().get("realtime_pull_index", True)


def get_realtime_index_mode() -> str:
    mode = str(load().get("realtime_index_mode", "core") or "core").lower()
    return mode if mode in {"core", "all"} else "core"


def get_realtime_index_symbols() -> list[str]:
    stored = load().get("realtime_index_symbols", SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    if isinstance(stored, str):
        import re
        stored = [s.strip() for s in re.split(r"[,\s]+", stored) if s.strip()]
    return [str(s) for s in stored if str(s).strip()]


def get_sse_refresh_pages() -> dict[str, bool]:
    """返回每个页面的 SSE 刷新开关。"""
    stored = load().get("sse_refresh_pages", {})
    # 合并默认值 (新增页面自动出现)
    result = dict(SSE_REFRESH_PAGES_DEFAULT)
    result.update({key: value for key, value in stored.items() if key in result})
    return result


def get_sidebar_index_symbols() -> list[str]:
    """返回左侧菜单显示的指数代码。"""
    stored = load().get("sidebar_index_symbols", SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    allowed = set(SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    return [s for s in stored if s in allowed]


def get_strategy_monitor_enabled() -> bool:
    """策略告警评估总开关。"""
    return load().get("strategy_monitor_enabled", False)


def get_system_notify_enabled() -> bool:
    """系统通知开关 — 开启后监控告警同时推送到操作系统通知中心。"""
    return load().get("system_notify_enabled", False)


def set_system_notify_enabled(enabled: bool) -> bool:
    """保存系统通知开关。"""
    save({"system_notify_enabled": bool(enabled)})
    return bool(enabled)


def get_line_target_id() -> str:
    return str(load().get("line_target_id", "") or "").strip()


def set_line_target_id(target_id: str) -> str:
    save({"line_target_id": str(target_id or "").strip()})
    return get_line_target_id()


def get_telegram_chat_id() -> str:
    return str(load().get("telegram_chat_id", "") or "").strip()


def set_telegram_chat_id(chat_id: str) -> str:
    save({"telegram_chat_id": str(chat_id or "").strip()})
    return get_telegram_chat_id()


def _get_notification_secret(key: str) -> str:
    from app import secrets_store
    return str(secrets_store.load().get(key, "") or "").strip()


def _set_notification_secret(key: str, value: str) -> str:
    from app import secrets_store
    value = str(value or "").strip()
    if value:
        secrets_store.save({key: value})
    else:
        secrets_store.clear(key)
    return _get_notification_secret(key)


def get_line_channel_access_token() -> str:
    return _get_notification_secret("line_channel_access_token")


def set_line_channel_access_token(token: str) -> str:
    return _set_notification_secret("line_channel_access_token", token)


def get_telegram_bot_token() -> str:
    return _get_notification_secret("telegram_bot_token")


def set_telegram_bot_token(token: str) -> str:
    return _set_notification_secret("telegram_bot_token", token)


def get_webhook_default_channels() -> list[str]:
    """新建监控规则时默认勾选的推送渠道 (多选)。

    空列表 = 新建规则默认不推送; ['line'] = 默认推播 LINE。
    此默认值供规则编辑器新建规则时预填, 单条规则仍可独立修改。

    舊版飛書/企業微信渠道與布林值只做安全過濾,不刪除原始偏好資料,
    也不自動映射到新的收件者。
    """
    d = load()
    raw = d.get("webhook_default_channels")
    if isinstance(raw, list):
        return [c for c in raw if c in REVIEW_PUSH_CHANNELS]
    return []


def set_webhook_default_channels(channels: list[str]) -> list[str]:
    """保存新建规则默认推送渠道 (多选)。过滤白名单外、去重、保序。空列表 = 不推送。"""
    seen: set[str] = set()
    cleaned: list[str] = []
    for c in channels or []:
        if c in REVIEW_PUSH_CHANNELS and c not in seen:
            seen.add(c)
            cleaned.append(c)
    save({"webhook_default_channels": cleaned})
    return cleaned


def get_screener_auto_run() -> bool:
    """选股页进入时是否自动运行所有策略 (获取命中数)。默认开。"""
    return load().get("screener_auto_run", True)


def get_strategy_monitor_ids() -> list[str]:
    """返回监控池中的策略 ID。"""
    return load().get("strategy_monitor_ids", [])


def set_realtime_monitor_config(cfg: dict) -> dict:
    """批量更新实时监控配置。"""
    updates = {}
    if "sse_refresh_pages" in cfg:
        updates["sse_refresh_pages"] = cfg["sse_refresh_pages"]
    if "strategy_monitor_enabled" in cfg:
        updates["strategy_monitor_enabled"] = cfg["strategy_monitor_enabled"]
    if "strategy_monitor_ids" in cfg:
        updates["strategy_monitor_ids"] = cfg["strategy_monitor_ids"]
    if "sidebar_index_symbols" in cfg:
        allowed = set(SIDEBAR_INDEX_SYMBOLS_DEFAULT)
        updates["sidebar_index_symbols"] = [s for s in cfg["sidebar_index_symbols"] if s in allowed]
    if "screener_auto_run" in cfg:
        updates["screener_auto_run"] = bool(cfg["screener_auto_run"])
    if "minute_intraday_refresh" in cfg:
        updates["minute_intraday_refresh"] = bool(cfg["minute_intraday_refresh"])
    if "minute_intraday_refresh_interval" in cfg:
        # clamp 到 [5, 60], 与 getter 一致, 防前端传越界值
        updates["minute_intraday_refresh_interval"] = max(
            _INTRADAY_REFRESH_INTERVAL_MIN,
            min(_INTRADAY_REFRESH_INTERVAL_MAX, int(cfg["minute_intraday_refresh_interval"])))
    if "monitor_ext_fields" in cfg:
        raw = cfg["monitor_ext_fields"] or {}
        updates["monitor_ext_fields"] = {
            "concept": _normalize_ext_field(raw.get("concept")),
            "industry": _normalize_ext_field(raw.get("industry")),
        }
    if updates:
        save(updates)
    return get_realtime_monitor_config()


def get_realtime_monitor_config() -> dict:
    """返回完整的实时监控配置。"""
    return {
        "sse_refresh_pages": get_sse_refresh_pages(),
        "strategy_monitor_enabled": get_strategy_monitor_enabled(),
        "strategy_monitor_ids": get_strategy_monitor_ids(),
        "sidebar_index_symbols": get_sidebar_index_symbols(),
        "screener_auto_run": get_screener_auto_run(),
        "minute_intraday_refresh": get_minute_intraday_refresh(),
        "minute_intraday_refresh_interval": get_minute_intraday_refresh_interval(),
        "monitor_ext_fields": get_monitor_ext_fields(),
    }


def get_nav_order() -> list[str]:
    """返回左侧菜单的自定义排序（内置页面 path + 扩展分析菜单 id）。"""
    return load().get("nav_order", [])


def set_nav_order(order: list[str]) -> list[str]:
    """保存左侧菜单排序。"""
    save({"nav_order": order})
    return get_nav_order()


def get_nav_hidden() -> list[str]:
    """返回左侧菜单中隐藏的项 id 列表。"""
    return load().get("nav_hidden", [])


def set_nav_hidden(hidden: list[str]) -> list[str]:
    """保存左侧菜单隐藏项。"""
    save({"nav_hidden": hidden})
    return get_nav_hidden()


def get_watchlist_columns() -> list[dict] | None:
    """返回自选列表列配置。"""
    return load().get("watchlist_columns")


def set_watchlist_columns(columns: list[dict]) -> list[dict]:
    """保存自选列表列配置。"""
    save({"watchlist_columns": columns})
    return columns


# ===== 首次使用引导 =====

def get_onboarding_completed() -> bool:
    """是否已完成首次使用向导。默认 False（新用户）。"""
    return bool(load().get("onboarding_completed", False))


def set_onboarding_completed(done: bool = True) -> bool:
    """标记首次使用向导完成状态。"""
    save({"onboarding_completed": bool(done)})
    return bool(done)
