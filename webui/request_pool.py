"""request_pool.py — 全局 HTTP 连接池、DNS 缓存与 Keep-Alive 极速复用器
======================================================================
解决多 Worker 高并发轮询 (如 Remail Pickup API / IMAP / 官方探针) 频繁建立 TCP/TLS
握手带来的高延迟与系统端口耗尽问题。

特性：
1. 全局 Connection Pool 复用 (Keep-Alive 长连接维持)
2. 线程安全的单例会话生命周期管理
3. 针对代理连接的缓存隔离机制
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("request_pool")

_pool_lock = threading.Lock()
_pooled_sessions: dict[str, Any] = {}
_session_last_used: dict[str, float] = {}
SESSION_MAX_IDLE_SECONDS = 180.0


def get_pooled_session(
    proxy: str = "",
    impersonate: str = "chrome136",
    user_agent: str = "",
) -> Any:
    """获取或新建一个复用的 curl_cffi 客户端 Session。"""
    try:
        from curl_cffi.requests import Session as CffiSession
    except ImportError:
        import requests
        return requests.Session()

    p_clean = str(proxy or "").strip()
    key = f"{p_clean}___{impersonate}"

    now = time.time()
    with _pool_lock:
        # 清理过期空闲连接
        expired_keys = [k for k, last_t in _session_last_used.items() if (now - last_t) > SESSION_MAX_IDLE_SECONDS]
        for k in expired_keys:
            try:
                s = _pooled_sessions.pop(k, None)
                if s and hasattr(s, "close"):
                    s.close()
            except Exception:
                pass
            _session_last_used.pop(k, None)

        if key in _pooled_sessions:
            _session_last_used[key] = now
            return _pooled_sessions[key]

        # 新建会话
        session = CffiSession(impersonate=impersonate)
        session.trust_env = False
        if p_clean:
            session.proxies = {"http": p_clean, "https": p_clean}
        if user_agent:
            session.headers.update({"User-Agent": user_agent})

        _pooled_sessions[key] = session
        _session_last_used[key] = now
        return session


def close_all_pooled_sessions():
    """关闭所有连接池中的会话。"""
    with _pool_lock:
        for s in _pooled_sessions.values():
            try:
                if hasattr(s, "close"):
                    s.close()
            except Exception:
                pass
        _pooled_sessions.clear()
        _session_last_used.clear()
