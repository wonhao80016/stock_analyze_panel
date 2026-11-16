"""设置 API — Key 配置 / 模式切换。

提供面向非开发者的 UI 配置入口,避免逼用户改 .env。

Phase 8B-FINAL: 移除已确认 zero 消费者且具明确风险的孤儿端点——
switch_endpoint(持久化任意呼叫者提供的 HTTPS URL 并重置 TickFlow 客户端指向
该端点,zero 前端消费者)、test_endpoint(对呼叫者提供的任意 URL 发起服务端
出站请求探测延迟,SSRF 形态,zero 前端消费者)、endpoints(仅服务已随
Data.tsx/EndpointTestDialog.tsx 一并删除的端点诊断功能, zero 消费者,一并
清理避免留下孤儿残片)、pipeline-schedule/instruments-schedule 两个偏好
setter(zero 前端消费者, 且会持久化配置并呼叫 scheduler.reschedule_job 操作
已被 Phase 8B-5.0.2 从调度器移除的 daily_pipeline/pre_market_instruments
job——目前虽因 job 不存在而实际上会抛错, 但一旦该 job id 未来被重新注册即
恢复危险)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import secrets_store
from app.data_providers.custom.config import MAX_TIMEOUT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("")
def get_settings() -> dict:
    """返回当前配置概况(Key 脱敏)。"""
    from app.config import settings
    from app.services import preferences
    from app.services.ai_provider import (
        ai_configured,
        current_ai_model,
        current_codex_command,
        current_codex_model,
        current_codex_reasoning_effort,
        current_openai_model,
        current_openai_reasoning_effort,
        current_ai_context_window,
        current_ai_max_output_tokens,
    )

    ai_provider = secrets_store.get_ai_config("ai_provider", settings.ai_provider)
    return {
        # 首次使用引导
        "onboarding_completed": preferences.get_onboarding_completed(),
        # AI 配置
        "ai_provider": ai_provider,
        "ai_base_url": secrets_store.get_ai_config("ai_base_url", settings.ai_base_url),
        "ai_api_key_masked": secrets_store.mask(secrets_store.get_ai_key()),
        "has_ai_key": bool(secrets_store.get_ai_key()),
        "ai_configured": ai_configured(ai_provider),
        "ai_model": current_ai_model(),
        "ai_openai_model": current_openai_model(),
        "ai_reasoning_effort": current_openai_reasoning_effort(),
        "ai_codex_model": current_codex_model(),
        "ai_codex_command": current_codex_command(),
        "ai_codex_reasoning_effort": current_codex_reasoning_effort(),
        "ai_user_agent": secrets_store.get_ai_config("ai_user_agent", settings.ai_user_agent),
        "ai_max_output_tokens": current_ai_max_output_tokens(),
        "ai_context_window": current_ai_context_window(),
    }


@router.post("/onboarding/complete")
def complete_onboarding() -> dict:
    """标记首次使用向导完成。

    写入 preferences.json,前端守卫据此判断是否需要再次展示向导。
    跨设备/清缓存安全 —— 状态落在后端文件,不依赖浏览器本地存储。
    """
    from app.services import preferences
    done = preferences.set_onboarding_completed(True)
    return {"ok": True, "onboarding_completed": done}


class AiSettingsIn(BaseModel):
    provider: str = "openai_compat"
    base_url: str = ""
    api_key: str | None = None
    model: str = ""
    reasoning_effort: str = Field(default="high", max_length=64)
    codex_command: str = ""
    codex_reasoning_effort: str = ""
    user_agent: str = ""
    max_output_tokens: int | None = None   # 输出上限, 钳制所有任务的 max_tokens
    context_window: int | None = None      # 输入上下文窗口上限 (约 token)


_NEW_URL_NEEDS_KEY = "變更 API 位址時必須同時填入該服務的 API Key, 已儲存的 Key 不會送往新位址"


def _moves_saved_key_to_new_url(req: AiSettingsIn) -> bool:
    """True when the request changes base_url but would reuse the saved key there."""
    from app.services import ai_provider

    if req.api_key is not None or not req.base_url.strip() or not secrets_store.get_ai_key():
        return False
    saved = ai_provider.current_ai_base_url()
    if not saved:
        return False
    return ai_provider.normalize_openai_base_url(req.base_url.strip()) != ai_provider.normalize_openai_base_url(saved)


@router.post("/ai")
def save_ai_settings(req: AiSettingsIn) -> dict:
    """保存 AI 配置（全部持久化到 secrets.json）"""
    from app.config import settings
    from app.services.ai_provider import (
        OPENAI_PROVIDER,
        ai_configured,
        current_ai_model,
        current_ai_provider,
        current_codex_command,
        current_codex_model,
        current_codex_reasoning_effort,
        current_openai_model,
        current_openai_reasoning_effort,
        current_ai_context_window,
        current_ai_max_output_tokens,
        normalize_codex_command,
        normalize_codex_model,
        normalize_codex_reasoning_effort,
        validate_codex_model,
    )

    if req.provider != "codex_cli" and _moves_saved_key_to_new_url(req):
        raise HTTPException(status_code=400, detail=_NEW_URL_NEEDS_KEY)
    if req.provider == "codex_cli":
        try:
            codex_model = validate_codex_model(normalize_codex_model(req.model))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    updates: dict = {}
    if req.provider:
        updates["ai_provider"] = req.provider
        settings.ai_provider = req.provider
    if req.provider == "codex_cli":
        updates["ai_codex_model"] = codex_model
        try:
            codex_command = normalize_codex_command(req.codex_command)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        codex_reasoning_effort = normalize_codex_reasoning_effort(req.codex_reasoning_effort)
        updates["ai_codex_command"] = codex_command
        updates["ai_codex_reasoning_effort"] = codex_reasoning_effort
        settings.ai_codex_command = codex_command
        settings.ai_codex_reasoning_effort = codex_reasoning_effort
    else:
        if req.base_url:
            updates["ai_base_url"] = req.base_url
            settings.ai_base_url = req.base_url
        if req.api_key is not None:
            if req.api_key:
                updates["ai_api_key"] = req.api_key
                settings.ai_api_key = req.api_key
            else:
                secrets_store.clear("ai_api_key")
                settings.ai_api_key = ""
        if req.model:
            updates["ai_model"] = req.model
            settings.ai_model = req.model
        if req.provider == OPENAI_PROVIDER:
            updates["ai_reasoning_effort"] = req.reasoning_effort.strip()
    # user_agent 允许清空(回到默认浏览器 UA),故无条件持久化
    updates["ai_user_agent"] = req.user_agent
    settings.ai_user_agent = req.user_agent

    # 输出上限 / 输入上下文窗口 (数值配置, 缺省保持原值)
    if req.max_output_tokens is not None:
        if req.max_output_tokens <= 0:
            raise HTTPException(status_code=400, detail="输出上限必须为正整数")
        updates["ai_max_output_tokens"] = req.max_output_tokens
        settings.ai_max_output_tokens = req.max_output_tokens
    if req.context_window is not None:
        if req.context_window <= 0:
            raise HTTPException(status_code=400, detail="上下文窗口必须为正整数")
        updates["ai_context_window"] = req.context_window
        settings.ai_context_window = req.context_window

    if updates:
        secrets_store.save(updates)

    provider = current_ai_provider()
    return {
        "ok": True,
        "ai_provider": provider,
        "ai_model": current_ai_model(),
        "ai_openai_model": current_openai_model(),
        "ai_reasoning_effort": current_openai_reasoning_effort(),
        "ai_codex_model": current_codex_model(),
        "ai_codex_command": current_codex_command(),
        "ai_codex_reasoning_effort": current_codex_reasoning_effort(),
        "ai_configured": ai_configured(provider),
        "ai_max_output_tokens": current_ai_max_output_tokens(),
        "ai_context_window": current_ai_context_window(),
    }


@router.post("/ai/test")
async def probe_ai_settings(req: AiSettingsIn) -> dict:
    """Send one short prompt with a candidate AI configuration; nothing is persisted.

    Blank fields fall back to the saved values exactly as POST /ai would keep them.
    """
    from openai import AsyncOpenAI

    from app.config import settings
    from app.services import ai_provider

    if req.provider == ai_provider.CODEX_CLI_PROVIDER:
        raise HTTPException(status_code=400, detail="Codex CLI 不支援測試未儲存的設定")
    for value, label in ((req.max_output_tokens, "输出上限"), (req.context_window, "上下文窗口")):
        if value is not None and value <= 0:
            raise HTTPException(status_code=400, detail=f"{label}必须为正整数")
    if _moves_saved_key_to_new_url(req):
        raise HTTPException(status_code=400, detail=_NEW_URL_NEEDS_KEY)

    base_url = (req.base_url or ai_provider.current_ai_base_url()).strip()
    api_key = secrets_store.get_ai_key() if req.api_key is None else req.api_key
    model = (req.model or ai_provider.current_openai_model()).strip()
    if not base_url:
        return {"ok": False, "error": "未設定 AI 服務: 請填寫 API 位址"}
    if not api_key:
        return {"ok": False, "error": "AI API Key 未配置, 请在设置页配置"}
    if not model:
        return {"ok": False, "error": "未設定 AI 模型: 請填寫模型"}

    max_output_tokens = req.max_output_tokens or ai_provider.current_ai_max_output_tokens()
    kwargs: dict = {"max_tokens": min(8, max_output_tokens), "temperature": 0}
    if req.provider == ai_provider.OPENAI_PROVIDER and req.reasoning_effort.strip():
        kwargs["reasoning_effort"] = req.reasoning_effort.strip()
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=ai_provider.normalize_openai_base_url(base_url),
        timeout=15,
        max_retries=0,
        default_headers={"User-Agent": req.user_agent or settings.ai_user_agent},
    )
    messages = [{"role": "user", "content": "Reply exactly: OK"}]
    try:
        async with client:
            while True:
                try:
                    resp = await client.chat.completions.create(model=model, messages=messages, **kwargs)
                    break
                except Exception as exc:
                    retry_kwargs = ai_provider._openai_retry_kwargs(exc, kwargs)
                    if retry_kwargs is None:
                        raise
                    kwargs = retry_kwargs
    except Exception as exc:
        error = ai_provider._format_openai_error(exc) if ai_provider._is_openai_transport_error(exc) else str(exc)
        return {"ok": False, "error": error or repr(exc), "error_type": type(exc).__name__}
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    return {"ok": True, "model": model, "response": text[:80]}


@router.delete("/ai")
def clear_ai_settings() -> dict:
    """一键清空 AI 配置(provider / base_url / api_key / model)。

    保留 ai_user_agent —— 自定义请求头与凭证解耦,清空凭证不影响绕过 CDN 拦截的设置。
    """
    from app.config import settings

    secrets_store.clear(
        "ai_provider",
        "ai_base_url",
        "ai_api_key",
        "ai_model",
        "ai_reasoning_effort",
        "ai_codex_model",
        "ai_codex_command",
        "ai_codex_reasoning_effort",
    )
    secrets_store.clear("ai_provider", "ai_base_url", "ai_api_key", "ai_model", "ai_codex_command", "ai_codex_reasoning_effort", "ai_max_output_tokens", "ai_context_window")
    # 同步重置运行时内存(provider 回默认值,其余置空)
    settings.ai_provider = "openai_compat"
    settings.ai_base_url = ""
    settings.ai_api_key = ""
    settings.ai_model = ""
    settings.ai_codex_command = "codex"
    settings.ai_codex_reasoning_effort = ""
    settings.ai_max_output_tokens = 8192
    settings.ai_context_window = 64000

    return {"ok": True}


# ===== 偏好设置 =====

def _realtime_allowed() -> bool:
    """当前档位是否允许实时行情(none/free 不允许)。"""
    from app.services.quote_service import QuoteService
    return QuoteService.is_realtime_allowed()


_OFFICIAL_PROVIDERS = {"taiwan"}
# Routing values and settings-page keys that a custom source name must not shadow.
_RESERVED_SOURCE_NAMES = _OFFICIAL_PROVIDERS | {"tickflow", "same_as_daily", "__new__"}


class DataProvidersIn(BaseModel):
    daily_data_provider: str | None = None
    adj_factor_provider: str | None = None
    minute_data_provider: str | None = None
    realtime_data_provider: str | None = None
    financial_data_provider: str | None = None


class PluginKeyIn(BaseModel):
    plugin: str
    api_key: str


class DatasetConfigIn(BaseModel):
    url: str
    method: str = "GET"
    batch: int | None = None
    rpm: int | None = None
    response_path: str = ""
    field_map: dict[str, str] = {}
    transforms: dict[str, str] = {}
    symbols_param: str = "symbols"
    start_param: str = "start_time"
    end_param: str = "end_time"
    asset_type_param: str | None = None
    freq_param: str | None = None
    units: dict[str, str] = {}
    timeout: float | None = Field(
        default=None,
        gt=0,
        le=MAX_TIMEOUT,
        allow_inf_nan=False,
    )


class AuthConfigIn(BaseModel):
    type: str = "none"
    token_env: str | None = None
    header: str = "Authorization"
    param: str = "token"


class CustomSourceIn(BaseModel):
    name: str
    display_name: str = ""
    auth: AuthConfigIn = AuthConfigIn()
    datasets: dict[str, DatasetConfigIn] = {}


class CustomSourceTestIn(BaseModel):
    provider: str
    dataset: str
    symbols: list[str] | None = None
    config: CustomSourceIn | None = None


@router.get("/preferences")
def get_preferences() -> dict:
    """返回用户偏好设置。"""
    from app.services import preferences
    return {
        "realtime_quotes_enabled": preferences.get_realtime_quotes_enabled(),
        "realtime_allowed": _realtime_allowed(),
        "taiwan_market_closed_today": _taiwan_market_closed_today(),
        "watchlist_groups_in_nav": preferences.get_watchlist_groups_in_nav(),
        "daily_data_provider": preferences.get_daily_data_provider(),
        "adj_factor_provider": preferences.get_adj_factor_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
        "watchlist_columns": preferences.get_watchlist_columns(),
        "sse_refresh_pages": preferences.get_sse_refresh_pages(),
        "strategy_monitor_enabled": preferences.get_strategy_monitor_enabled(),
        "strategy_monitor_ids": preferences.get_strategy_monitor_ids(),
        "system_notify_enabled": preferences.get_system_notify_enabled(),
        "line_target_id": preferences.get_line_target_id(),
        "line_channel_access_token_masked": secrets_store.mask(preferences.get_line_channel_access_token()),
        "line_configured": bool(preferences.get_line_target_id() and preferences.get_line_channel_access_token()),
        "telegram_chat_id": preferences.get_telegram_chat_id(),
        "telegram_bot_token_masked": secrets_store.mask(preferences.get_telegram_bot_token()),
        "telegram_configured": bool(preferences.get_telegram_chat_id() and preferences.get_telegram_bot_token()),
        "webhook_default_channels": preferences.get_webhook_default_channels(),
        "sidebar_index_symbols": preferences.get_sidebar_index_symbols(),
        "minute_intraday_refresh": preferences.get_minute_intraday_refresh(),
        "minute_intraday_refresh_interval": preferences.get_minute_intraday_refresh_interval(),
        "monitor_ext_fields": preferences.get_monitor_ext_fields(),
        "nav_order": preferences.get_nav_order(),
        "nav_hidden": preferences.get_nav_hidden(),
        "screener_auto_run": preferences.get_screener_auto_run(),
    }


@router.get("/data-sources")
def list_data_sources() -> dict:
    """列出已加载的数据源 (内置 / 插件 / 用户自定义)。"""
    from app.data_providers import custom as custom_sources
    return {
        "builtin": [{"name": "taiwan", "display_name": "台灣官方資料源 (TWSE/TPEx)", "datasets": ["daily", "realtime", "financial"]}],
        "plugins": custom_sources.list_plugins(),
        "custom": custom_sources.list_sources(),
        "errors": custom_sources.errors(),
        "config_dir": str(custom_sources.data_sources_dir()),
    }


@router.post("/plugin-key")
def save_plugin_key(req: PluginKeyIn) -> dict:
    """保存插件 API Key(先探后存)。

    流程: probe_plugin_key 用候选 Key 实探 → 有效才写 secrets.json
    ({plugin}_api_key, 优先级高于 .env) → load_all 重扫, 插件即刻变为可切换。
    """
    from app.data_providers import custom as custom_sources

    name = _checked_plugin_name(req.plugin.strip())
    key = req.api_key.strip()
    if not key:
        return {"ok": False, "error": "key empty"}
    ok, message = custom_sources.probe_plugin_key(name, key)
    if not ok:
        return {"ok": False, "reason": "invalid", "error": message}
    secrets_store.save({f"{name}_api_key": key})
    custom_sources.load_all()
    status = next((p for p in custom_sources.list_plugins() if p["name"] == name), None)
    return {
        "ok": True,
        "api_key_masked": secrets_store.mask(key),
        "plugin_available": bool(status and status.get("available")),
        "plugin": status,
    }


@router.delete("/plugin-key/{name}")
def clear_plugin_key(name: str) -> dict:
    """清除插件的界面配置 Key(secrets.json);.env 里的同名变量仍然生效。"""
    from app.data_providers import custom as custom_sources

    name = _checked_plugin_name(name)
    manifest = custom_sources.plugin_manifest(name)
    if manifest is None or not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    if not manifest.get("api_key_env"):
        raise HTTPException(status_code=400, detail=f"插件 '{name}' 不支持在界面配置 Key")
    secrets_store.clear(f"{name.lower()}_api_key")
    custom_sources.load_all()
    status = next((p for p in custom_sources.list_plugins() if p["name"] == name), None)
    return {
        "ok": True,
        "plugin_available": bool(status and status.get("available")),
        "plugin": status,
    }


@router.post("/data-sources/reload")
def reload_data_sources() -> dict:
    """重新加载 data_sources/*.yaml。"""
    from app.data_providers import custom as custom_sources
    custom_sources.load_all()
    return list_data_sources()


@router.post("/plugins/{name}/install")
def install_plugin(name: str) -> dict:
    """安装指定插件的依赖 (npm install / pip install), 完成后重新扫描。

    根据 plugin.yaml 的 runtime 字段决定安装方式。安装可能耗时较长 (网络下载),
    客户端需设较长超时。
    """
    from app.data_providers import custom as custom_sources
    name = _checked_plugin_name(name)
    if not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    ok, message = custom_sources.install_plugin(name)
    # 无论成功失败都重新扫描, 刷新插件状态 (安装可能部分成功)
    custom_sources.load_all()
    result = list_data_sources()
    result["install_ok"] = ok
    result["install_message"] = message
    return result


@router.delete("/plugins/{name}/install")
def uninstall_plugin(name: str) -> dict:
    """卸载指定插件的依赖 (删除 node_modules / pip uninstall), 完成后重新扫描。

    如果该插件当前正被使用, 自动回退到 taiwan。
    """
    from app.data_providers import custom as custom_sources
    from app.services import preferences
    name = _checked_plugin_name(name)
    if not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    ok, message = custom_sources.uninstall_plugin(name)
    # 卸载后若该插件正被使用, 回退 taiwan
    for getter, key, default in [
        (preferences.get_daily_data_provider, "daily_data_provider", "taiwan"),
        (preferences.get_minute_data_provider, "minute_data_provider", "taiwan"),
        (preferences.get_realtime_data_provider, "realtime_data_provider", "taiwan"),
        (preferences.get_financial_provider, "financial_data_provider", "taiwan"),
    ]:
        if getter() == name:
            preferences.save({key: default})
    custom_sources.load_all()
    result = list_data_sources()
    result["uninstall_ok"] = ok
    result["uninstall_message"] = message
    return result


@router.get("/data-sources/{name}")
def get_data_source(name: str) -> dict:
    """读取一个自定义数据源的完整配置(用于前端编辑回填)。"""
    from app.data_providers import custom as custom_sources
    cfg = custom_sources.get_config_dict(name) or custom_sources.failed_config_dict(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
    return cfg


@router.post("/data-sources")
def save_data_source(req: CustomSourceIn) -> dict:
    """新建一个自定义数据源 yaml; 名称已被占用时回 409, 不覆盖既有设定。"""
    from app.data_providers import custom as custom_sources
    name = _checked_source_name(req.name)
    base = custom_sources.data_sources_dir()
    if (
        name in _RESERVED_SOURCE_NAMES
        or custom_sources.is_builtin(name)
        or name in custom_sources.names()
        or _source_files_declaring(name)
        or (base / f"{name}.yaml").exists()
        or (base / f"{name}.yml").exists()
    ):
        raise HTTPException(status_code=409, detail=f"資料來源名稱「{name}」已被使用")
    return _write_data_source(req, base / f"{name}.yaml")


@router.put("/data-sources/{name}")
def update_data_source(name: str, req: CustomSourceIn) -> dict:
    """更新一个既有的自定义数据源 yaml(含载入失败的), 保存后自动 reload。"""
    from app.data_providers import custom as custom_sources
    name = _checked_source_name(name)
    if (req.name or "").lower() != name:
        raise HTTPException(status_code=400, detail="不能在編輯時更改資料來源名稱")
    existing = [] if custom_sources.is_builtin(name) else _source_files_declaring(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
    if len(existing) > 1:
        files = ", ".join(p.name for p in existing)
        raise HTTPException(status_code=409, detail=f"有多個設定檔宣告了資料來源「{name}」({files}), 請先手動合併或刪除多餘的檔案")
    return _write_data_source(req, existing[0])


def _checked_plugin_name(raw: str) -> str:
    """Lowercased plugin name; rejected before it reaches the plugin loader."""
    from app.data_providers.custom.loader import _NAME_RE

    name = raw.lower()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail=f"插件名稱「{raw}」只能使用小寫字母、數字與底線")
    return name


def _checked_source_name(raw: str | None) -> str:
    """Lowercased source name; rejected before any path is built from it."""
    from app.data_providers.custom.loader import _NAME_RE

    name = (raw or "").lower()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail=f"資料來源名稱「{raw}」只能使用小寫字母、數字與底線")
    return name


def _source_files_declaring(name: str) -> list[Path]:
    """Config files whose content declares *name*, whatever their file name or suffix.

    A file that cannot be parsed counts as declaring its stem, the name the loader would give it.
    """
    from app.data_providers import custom as custom_sources
    from app.data_providers.custom.config import load_config

    base = custom_sources.data_sources_dir()
    found: list[Path] = []
    for file in sorted([*base.glob("*.yaml"), *base.glob("*.yml")]):
        try:
            declared = load_config(file).name
        except Exception:
            declared = file.stem.lower()
        if declared == name:
            found.append(file.resolve())
    return found


def _write_data_source(req: CustomSourceIn, target: Path) -> dict:
    """Validate the config and atomically write it to *target* inside the data source directory."""
    import yaml

    from app.data_providers import custom as custom_sources
    from app.data_providers.custom.loader import _sanitize_for_yaml
    from app.taiwan.universe.service import write_atomically

    config = req.model_dump()
    config["name"] = (config.get("name") or "").lower()
    base = custom_sources.data_sources_dir().resolve()
    target = target.resolve()
    if target.parent != base:
        raise HTTPException(status_code=400, detail="invalid data source path")
    try:
        errors = custom_sources.validate_config(config)
        if errors:
            raise ValueError("資料來源設定無法載入, 未儲存: " + "; ".join(errors))
        text = yaml.safe_dump(_sanitize_for_yaml(config), allow_unicode=True, sort_keys=False)
        write_atomically(target, lambda tmp: tmp.write_text(text, encoding="utf-8"))
        custom_sources.load_all()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return list_data_sources()


@router.delete("/data-sources/{name}")
def delete_data_source(name: str) -> dict:
    """删除一个自定义数据源 yaml, 保存后自动 reload。

    若当前总开关选中的就是被删的源, 回退到 taiwan。
    """
    from app.data_providers import custom as custom_sources
    from app.services import preferences
    try:
        custom_sources.delete_config(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    custom_sources.load_all()
    # 回退被删源的偏好
    updates: dict = {}
    if preferences.get_daily_data_provider() == name:
        updates["daily_data_provider"] = "taiwan"
    if preferences.get_realtime_data_provider() == name:
        updates["realtime_data_provider"] = "taiwan"
    if preferences.get_financial_provider() == name:
        updates["financial_data_provider"] = "taiwan"
    adj = preferences.get_adj_factor_provider()
    if adj == name:
        updates["adj_factor_provider"] = "same_as_daily"
    if updates:
        preferences.save(updates)
    return list_data_sources()


@router.post("/data-sources/test")
def test_data_source(req: CustomSourceTestIn) -> dict:
    """试拉自定义数据源，不写盘。"""
    from app.data_providers import custom as custom_sources

    temporary = req.config is not None
    provider = None
    try:
        if req.config:
            config = req.config.model_dump()
            dataset_config = config["datasets"].get(req.dataset)
            if dataset_config is None:
                raise ValueError(f"dataset '{req.dataset}' is not configured")
            config["datasets"] = {req.dataset: dataset_config}
            provider = custom_sources.create_provider(config)
        else:
            provider = custom_sources.get_provider(req.provider)
        return provider.test_dataset(req.dataset, req.symbols)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"自定义数据源测试失败: {e}") from e
    finally:
        if temporary and provider is not None:
            provider.close()


@router.put("/preferences/data-providers")
def update_data_providers(req: DataProvidersIn) -> dict:
    """保存数据源选择。"""
    from app.services import preferences
    updates = req.model_dump(exclude_none=True)
    if updates.get("daily_data_provider", "taiwan").lower() not in _OFFICIAL_PROVIDERS:
        raise HTTPException(status_code=400, detail="台股日K固定由台灣官方資料源提供, 不能切換")
    if updates.get("adj_factor_provider", "same_as_daily").lower() not in _OFFICIAL_PROVIDERS | {"same_as_daily"}:
        raise HTTPException(status_code=400, detail="台股除權因子固定來自公司行動表, 不能切換")
    if updates:
        preferences.save(updates)
    return {
        "daily_data_provider": preferences.get_daily_data_provider(),
        "adj_factor_provider": preferences.get_adj_factor_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
    }


@router.get("/preferences/watchlist-columns")
def get_watchlist_columns() -> dict:
    """返回自选列表列配置。"""
    from app.services import preferences
    cols = preferences.get_watchlist_columns()
    return {"columns": cols}


class NavOrderIn(BaseModel):
    nav_order: list[str]


class NavHiddenIn(BaseModel):
    nav_hidden: list[str]


@router.put("/preferences/nav-order")
def update_nav_order(req: NavOrderIn) -> dict:
    """保存左侧菜单排序（内置页面 path + 扩展分析菜单 id 的有序列表）。"""
    from app.services import preferences
    saved = preferences.set_nav_order(req.nav_order)
    return {"nav_order": saved}


@router.put("/preferences/nav-hidden")
def update_nav_hidden(req: NavHiddenIn) -> dict:
    """保存左侧菜单隐藏项。"""
    from app.services import preferences
    saved = preferences.set_nav_hidden(req.nav_hidden)
    return {"nav_hidden": saved}


@router.put("/preferences/watchlist-columns")
def update_watchlist_columns(req: dict) -> dict:
    """保存自选列表列配置。"""
    from app.services import preferences
    columns = req.get("columns", [])
    saved = preferences.set_watchlist_columns(columns)
    return {"columns": saved}


class RealtimeQuotesPrefs(BaseModel):
    realtime_quotes_enabled: bool


class MarketClosedTodayPrefs(BaseModel):
    closed: bool


def _taiwan_market_closed_today() -> bool:
    from app.services import preferences
    from app.taiwan.realtime.calendar import taipei_today

    return preferences.get_taiwan_market_closed_date() == taipei_today().isoformat()


@router.put("/preferences/market-closed-today")
def update_market_closed_today(req: MarketClosedTodayPrefs) -> dict:
    """Mark today (Asia/Taipei) as an unscheduled Taiwan market closure, which stops quote polling."""
    from app.services import preferences
    from app.taiwan.realtime.calendar import taipei_today

    preferences.set_taiwan_market_closed_date(taipei_today().isoformat() if req.closed else None)
    return {"taiwan_market_closed_today": _taiwan_market_closed_today()}


@router.put("/preferences/realtime-quotes")
def update_realtime_quotes(req: RealtimeQuotesPrefs, request: Request) -> dict:
    """保存全局实时行情开关。

    none 档无实时行情权限；free 档开启自选股实时；starter+ 开启全市场实时。
    前端据此把开关置灰 / 回弹。
    """
    from app.services import preferences
    qs = getattr(request.app.state, "quote_service", None)

    allowed = qs.is_realtime_allowed() if qs else True
    if req.realtime_quotes_enabled and not allowed:
        # 当前档位不允许开启实时行情 — 强制关闭
        if qs:
            qs.disable()
        preferences.save({"realtime_quotes_enabled": False})
        return {"realtime_quotes_enabled": False, "realtime_allowed": False}
    if req.realtime_quotes_enabled and qs and qs.is_paused():
        # Realtime quotes stay off while the quote service is paused, to avoid write races.
        raise HTTPException(status_code=409, detail="数据同步运行中，实时行情已临时暂停，请稍后再开启")

    if qs and not req.realtime_quotes_enabled:
        qs.disable()
    preferences.save({"realtime_quotes_enabled": req.realtime_quotes_enabled})
    if qs and req.realtime_quotes_enabled:
        qs.enable()
    return {"realtime_quotes_enabled": req.realtime_quotes_enabled, "realtime_allowed": allowed}


class WatchlistGroupsInNavPrefs(BaseModel):
    watchlist_groups_in_nav: bool


@router.put("/preferences/watchlist-groups-in-nav")
def update_watchlist_groups_in_nav(req: WatchlistGroupsInNavPrefs) -> dict:
    """保存自选分组是否显示在侧边栏开关。"""
    from app.services import preferences
    preferences.save({"watchlist_groups_in_nav": req.watchlist_groups_in_nav})
    return {"watchlist_groups_in_nav": req.watchlist_groups_in_nav}


class RealtimeMonitorConfigIn(BaseModel):
    sse_refresh_pages: dict[str, bool] | None = None
    strategy_monitor_enabled: bool | None = None
    strategy_monitor_ids: list[str] | None = None
    sidebar_index_symbols: list[str] | None = None
    screener_auto_run: bool | None = None
    minute_intraday_refresh: bool | None = None
    minute_intraday_refresh_interval: int | None = None
    monitor_ext_fields: dict | None = None


@router.put("/preferences/realtime-monitor")
def update_realtime_monitor_config(req: RealtimeMonitorConfigIn, request: Request) -> dict:
    """更新实时监控配置。策略监控统一迁移为 MonitorRule,由监控引擎评估。"""
    from app.services import preferences

    cfg = req.model_dump(exclude_none=True)
    result = preferences.set_realtime_monitor_config(cfg)

    # 策略监控开关/池变化 → 同步迁移为 type=strategy 规则 + reload 引擎
    if req.strategy_monitor_ids is not None or req.strategy_monitor_enabled is not None:
        monitor_engine = getattr(request.app.state, "monitor_engine", None)
        strategy_engine = getattr(request.app.state, "strategy_engine", None)
        data_dir = request.app.state.repo.store.data_dir
        if monitor_engine is not None and strategy_engine is not None:
            from app.strategy import monitor_rules as mr_store
            try:
                if preferences.get_strategy_monitor_enabled():
                    ids = preferences.get_strategy_monitor_ids()
                    names = {s.id: s.name for s in strategy_engine.list_strategies()}
                    mr_store.migrate_strategy_monitors(data_dir, ids, names)
                else:
                    # 关闭策略监控: 停用所有策略规则
                    mr_store.migrate_strategy_monitors(data_dir, [], {})
                # reload 规则到引擎
                monitor_engine.set_rules(mr_store.load_all(data_dir))
            except Exception:
                pass

    return result


class QuoteIntervalIn(BaseModel):
    interval: float


class SystemNotifyPrefsIn(BaseModel):
    enabled: bool


@router.put("/preferences/system-notify")
def update_system_notify(req: SystemNotifyPrefsIn) -> dict:
    """系统通知开关 — 开启后监控告警同时推送到操作系统通知中心。

    纯偏好, 无副作用 (不像策略监控要迁移规则), 直接落盘即可。
    quote_service 在每轮告警评估时读此开关决定是否发系统通知。
    """
    from app.services import preferences
    saved = preferences.set_system_notify_enabled(req.enabled)
    return {"system_notify_enabled": saved}


class NotificationChannelPrefsIn(BaseModel):
    recipient: str = ""
    token: str | None = None
    clear_token: bool = False


def _notification_channel_response(channel: str) -> dict:
    from app.services import preferences

    if channel == "line":
        recipient = preferences.get_line_target_id()
        token = preferences.get_line_channel_access_token()
        return {
            "line_target_id": recipient,
            "line_channel_access_token_masked": secrets_store.mask(token),
            "line_configured": bool(recipient and token),
        }
    recipient = preferences.get_telegram_chat_id()
    token = preferences.get_telegram_bot_token()
    return {
        "telegram_chat_id": recipient,
        "telegram_bot_token_masked": secrets_store.mask(token),
        "telegram_configured": bool(recipient and token),
    }


@router.put("/preferences/line-messaging")
def update_line_messaging(req: NotificationChannelPrefsIn) -> dict:
    from app.services import preferences

    preferences.set_line_target_id(req.recipient)
    if req.clear_token:
        preferences.set_line_channel_access_token("")
    elif req.token and req.token.strip():
        preferences.set_line_channel_access_token(req.token)
    return _notification_channel_response("line")


@router.put("/preferences/telegram-bot")
def update_telegram_bot(req: NotificationChannelPrefsIn) -> dict:
    from app.services import preferences

    preferences.set_telegram_chat_id(req.recipient)
    if req.clear_token:
        preferences.set_telegram_bot_token("")
    elif req.token and req.token.strip():
        preferences.set_telegram_bot_token(req.token)
    return _notification_channel_response("telegram")


@router.post("/preferences/line-messaging/test")
def test_line_messaging() -> dict:
    from app.services import preferences, webhook_adapter

    ok = webhook_adapter.send_line(
        preferences.get_line_channel_access_token(),
        preferences.get_line_target_id(),
        "測試通知",
        "LINE Messaging API 已成功連線。",
    )
    return {"ok": ok}


@router.post("/preferences/telegram-bot/test")
def test_telegram_bot() -> dict:
    from app.services import preferences, webhook_adapter

    ok = webhook_adapter.send_telegram(
        preferences.get_telegram_bot_token(),
        preferences.get_telegram_chat_id(),
        "測試通知",
        "Telegram Bot API 已成功連線。",
    )
    return {"ok": ok}


class WebhookDefaultChannelsIn(BaseModel):
    channels: list[str]  # 多选: ['line','telegram']; 空数组=默认不推送


@router.put("/preferences/webhook-default-channels")
def update_webhook_default_channels(req: WebhookDefaultChannelsIn) -> dict:
    """新建监控规则时默认勾选的推送渠道 (多选)。

    作为新建规则的默认推送渠道预填, 单条规则仍可在规则编辑页独立修改。
    空数组=默认不推送。白名单外的渠道会被过滤掉。
    """
    from app.services import preferences

    saved = preferences.set_webhook_default_channels(req.channels)
    return {"webhook_default_channels": saved}


@router.put("/preferences/quote-interval")
def update_quote_interval(req: QuoteIntervalIn, request: Request) -> dict:
    """更新行情轮询间隔。按档位自动 clamp。"""
    qs = request.app.state.quote_service
    clamped = qs.set_interval(req.interval)
    return {
        "interval": clamped,
        "min_interval": qs.get_min_interval(),
        "max_interval": qs.MAX_INTERVAL,
    }


@router.get("/preferences/quote-interval")
def get_quote_interval(request: Request) -> dict:
    """获取当前行情轮询间隔和档位限制。"""
    qs = getattr(request.app.state, "quote_service", None)
    if not qs:
        return {"interval": 6.0, "min_interval": 6.0, "max_interval": 60.0}
    return {
        "interval": qs._interval,
        "min_interval": qs.get_min_interval(),
        "max_interval": qs.MAX_INTERVAL,
    }
