# -*- coding: utf-8 -*-
"""
统一响应、限流与鉴权中间件（文档第 10/13 章）。
- 统一响应：{"code":0,"msg":"ok","data":{...}}
- 写接口幂等 + 限流（NFR-007，≥20 QPS 保护）
- 鉴权：Bearer Token + 用户登录 Token（管理操作需 admin）
"""
import time
import json
import hashlib
import functools
import threading
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config.settings import settings

# ---- 统一响应 ----
def ok(data=None, msg="ok"):
    return {"code": 0, "msg": msg, "data": data}


def err(code, msg):
    return {"code": code, "msg": msg, "data": None}


def wrap(handler):
    """把路由函数返回值统一包成 {code,msg,data}（若已是该结构则原样返回）。"""
    @functools.wraps(handler)
    def inner(*a, **k):
        res = handler(*a, **k)
        if isinstance(res, dict) and set(res.keys()) == {"code", "msg", "data"}:
            return res
        return ok(res)
    return inner


# ---- 限流：滑动窗口（进程内，单实例够用）----
class RateLimiter:
    def __init__(self, qps=20, burst=40):
        self.qps = qps
        self.burst = burst
        self._calls = []
        self._lock = threading.Lock()

    def allow(self, key="default"):
        now = time.time()
        with self._lock:
            # 清掉 1 秒前的记录
            while self._calls and now - self._calls[0] > 1.0:
                self._calls.pop(0)
            if len(self._calls) >= self.burst:
                return False
            # 每秒平均不超过 qps
            if len(self._calls) >= self.qps:
                return False
            self._calls.append(now)
            return True


# 写接口限流器（保守：20 QPS / 40 burst）
_write_limiter = RateLimiter(qps=20, burst=40)


def require_rate_limit(request: Request):
    """对 POST/PUT 写接口做限流保护，超限返回 429。"""
    if request.method in ("POST", "PUT"):
        if not _write_limiter.allow(key="write"):
            return JSONResponse(status_code=429,
                                content=err(429, "请求过于频繁，已被限流"))
    return None


# ---- 鉴权 ----
# 用户 Token 表（内存，登录签发）：token -> {username, role, exp}
_user_tokens = {}
_user_lock = threading.Lock()


def _issue_token(username, role):
    tok = hashlib.sha256(f"{username}:{time.time()}:{os.urandom(8).hex()}".encode()).hexdigest()[:40]
    with _user_lock:
        _user_tokens[tok] = {"username": username, "role": role, "exp": time.time() + 7200}
    return tok


import os  # noqa: E402  (urandom)


def login(username, password):
    """校验用户密码，成功返回 token 与角色。"""
    admin = settings.get("auth", "admin", default={})
    user = settings.get("auth", "user", default={})
    if username == admin.get("username") and password == admin.get("password"):
        return _issue_token(username, "admin"), "admin"
    if username == user.get("username") and password == user.get("password"):
        return _issue_token(username, "user"), "user"
    return None, None


def get_token(request: Request):
    """从 Authorization: Bearer <tok> 提取 token。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_auth(request: Request, need_admin=False):
    """鉴权依赖：校验 Bearer token（api_token 或用户 token），返回响应或 None。"""
    token = get_token(request)
    if not token:
        return JSONResponse(status_code=401, content=err(401, "未提供有效凭证"))
    # 1) 系统 API token（文档对外接口 Bearer Token）
    api_token = settings.get("server", "api_token")
    if token == api_token:
        # api_token 视为管理员级（用于外部业务系统接入）
        request.state.role = "admin"
        return None
    # 2) 用户登录 token
    with _user_lock:
        info = _user_tokens.get(token)
        if info and info["exp"] > time.time():
            request.state.role = info["role"]
        else:
            return JSONResponse(status_code=401, content=err(401, "凭证无效或已过期"))
    if need_admin and request.state.role != "admin":
        return JSONResponse(status_code=403, content=err(403, "需要管理员权限"))
    return None


def get_role(request: Request):
    """当前请求角色（默认 user）。"""
    return getattr(request.state, "role", "user")
