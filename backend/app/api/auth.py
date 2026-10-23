"""访问认证 API。

端点:
  GET  /api/auth/status        — 是否已设密码、当前会话是否有效
  POST /api/auth/setup         — 首次设置密码(仅限本机, 防他人抢占)
  POST /api/auth/login         — 登录(密码 → 会话 token, 含限流)
  POST /api/auth/logout        — 注销当前会话
  POST /api/auth/change-password — 改密码(需已登录)

安全:
  - setup 端点只接受本机请求(客户端为 127.0.0.1/::1 且 Host 为回环名称), 其余 403。
    否则他人可比用户更早连上面板, 抢先设密码, 反客为主。
  - login 限流: 同一来源 IP 连续失败 5 次, 锁 5 分钟(内存计数)。
  - 会话 token 通过 HttpOnly cookie 下发, 前端无需手动管理。
"""
from __future__ import annotations

import ipaddress
import logging
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "tf_session"
SETUP_LOCAL_ONLY_DETAIL = (
    "未设访问密码时仅允许本机(127.0.0.1 或 ::1)访问。"
    "请先在本机浏览器设置密码,或以 AUTH_PASSWORD 环境变量预设密码后重启服务"
)
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 与 SESSION_TTL 一致

# 限流: { ip: (fail_count, lock_until_ts) }
_fail_counter: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_fail_lock = Lock()
_MAX_FAILS = 5
_LOCK_SECONDS = 300

_LOOPBACK_IPS = frozenset({ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")})
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _client_ip(request: Request) -> str:
    """Peer address as resolved by the ASGI server; never parse X-Forwarded-For here.

    uvicorn already substitutes the right-most untrusted XFF entry, only for peers in FORWARDED_ALLOW_IPS.
    """
    return request.client.host if request.client and request.client.host else "unknown"


def _is_loopback_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip in _LOOPBACK_IPS


def _parse_authority(value: str) -> tuple[str, int | None] | None:
    """Split a Host header value into (lowercase hostname, port); None if malformed."""
    if not value or "@" in value:
        return None
    try:
        parts = urlsplit(f"//{value}")
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, port


def _is_local_request(request: Request) -> bool:
    """Loopback peer that also addresses the panel by a loopback name.

    The Host check stops DNS rebinding: a page on attacker.example resolved to
    127.0.0.1 reaches us from a loopback peer but keeps its own Host name.
    """
    if not _is_loopback_ip(_client_ip(request)):
        return False
    # Deny-only use of XFF: a loopback peer outside FORWARDED_ALLOW_IPS (e.g. ::ffff:127.0.0.1
    # on a dual-stack bind) is a proxy uvicorn did not resolve, so every recorded hop must be loopback.
    hops = [
        hop.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for hop in header.split(",")
    ]
    if not all(_is_loopback_ip(hop) for hop in hops):
        return False
    authority = _parse_authority(request.headers.get("host", ""))
    return authority is not None and authority[0] in _LOOPBACK_HOSTNAMES


def _is_cross_origin(request: Request) -> bool:
    """True when a browser Origin header is present and differs from the panel's own Host.

    Only host and port are compared, because a TLS-terminating proxy makes the
    backend see http while the browser reports https.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return False
    try:
        parts = urlsplit(origin)
        origin_port = parts.port
    except ValueError:
        return True
    default_port = _DEFAULT_PORTS.get(parts.scheme)
    if default_port is None or not parts.hostname:
        return True
    authority = _parse_authority(request.headers.get("host", ""))
    if authority is None:
        return True
    host, host_port = authority
    return (parts.hostname, origin_port or default_port) != (host, host_port or default_port)


def auth_store_corrupt_response(exc: auth.AuthStoreCorruptError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": "AUTH_STORE_CORRUPT"})


def _check_login_rate_limit(ip: str) -> None:
    """登录失败限流检查, 触发则抛 429。锁定过期后重置计数(重新给 5 次机会)。"""
    with _fail_lock:
        count, until = _fail_counter.get(ip, (0, 0.0))
        now = time.time()
        if until > now:
            wait = int(until - now)
            raise HTTPException(
                status_code=429,
                detail=f"登录失败次数过多, 请 {wait} 秒后重试",
            )
        if until and until <= now:
            # 锁定已过期: 清除旧计数, 否则之后每失败一次都会立刻再锁 5 分钟
            _fail_counter.pop(ip, None)


def _record_login_fail(ip: str) -> None:
    """记录一次登录失败, 达阈值则锁定。"""
    with _fail_lock:
        # 防内存膨胀: 条目过多时清掉已过锁定期的记录
        if len(_fail_counter) > 1000:
            now = time.time()
            for stale in [k for k, (_, u) in _fail_counter.items() if u <= now]:
                _fail_counter.pop(stale, None)
        count, until = _fail_counter.get(ip, (0, 0.0))
        count += 1
        if count >= _MAX_FAILS:
            until = time.time() + _LOCK_SECONDS
            logger.warning("auth login locked for %s after %d fails", ip, count)
        _fail_counter[ip] = (count, until)


def _clear_login_fails(ip: str) -> None:
    """登录成功后清除该 IP 的失败计数。"""
    with _fail_lock:
        _fail_counter.pop(ip, None)


# ================================================================
# 端点
# ================================================================

class PasswordIn(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@router.get("/status")
def auth_status(request: Request) -> dict:
    """认证状态: 是否已设密码 + 当前请求是否已登录。"""
    token = request.cookies.get(COOKIE_NAME)
    return {
        "configured": auth.is_configured(),
        "authenticated": bool(token and auth.is_valid_session(token)),
    }


@router.post("/setup")
def setup_password(req: PasswordIn, request: Request) -> dict:
    """首次设置访问密码。仅限本机请求(防他人抢占)。

    若已设置过密码, 返回 409(改密码走 /change-password)。
    """
    client_ip = _client_ip(request)
    if not _is_local_request(request):
        logger.warning(
            "setup rejected from non-local request: ip=%s host=%s",
            client_ip,
            request.headers.get("host", ""),
        )
        raise HTTPException(status_code=403, detail=SETUP_LOCAL_ONLY_DETAIL)

    if not auth.set_initial_password(req.password):
        raise HTTPException(status_code=409, detail="密码已设置,如需修改请登录后使用改密码功能")

    logger.info("access password set up from %s", client_ip)
    return {"ok": True, "configured": True}


@router.post("/login")
def login(req: LoginIn, request: Request, response: Response) -> dict:
    """登录: 密码 → 会话 token(写 HttpOnly cookie)。含失败限流。"""
    ip = _client_ip(request)
    _check_login_rate_limit(ip)

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未设置访问密码")

    token = auth.verify_and_create_session(req.password)
    if not token:
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="密码错误")

    _clear_login_fails(ip)
    # HttpOnly: 防 XSS 窃取; SameSite=Lax: 防 CSRF; Path=/: 全站生效
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # 自托管可能无 HTTPS, 不强制 secure(建议反代加 HTTPS)
    )
    return {"ok": True, "authenticated": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    """注销当前会话。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(req: ChangePasswordIn, request: Request) -> dict:
    """修改密码: 需验证旧密码, 成功后所有会话失效(含当前, 需重新登录)。"""
    token = request.cookies.get(COOKIE_NAME)
    if not (token and auth.is_valid_session(token)):
        raise HTTPException(status_code=401, detail="请先登录")

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未设置访问密码")

    # 验证旧密码
    new_token = auth.verify_and_create_session(req.old_password)
    if not new_token:
        ip = _client_ip(request)
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="旧密码错误")
    # 临时 token 用完即弃
    auth.revoke_session(new_token)

    # 改密码(set_password 会清空所有会话)
    auth.set_password(req.new_password)
    return {"ok": True, "message": "密码已修改, 请重新登录"}
