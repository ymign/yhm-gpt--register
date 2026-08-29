"""proxy_util.py — 动态住宅代理与会话 IP 隔离重写引擎
=========================================================
支持常见动态住宅代理格式（-region-XX- / -sid-XXX / -session-XXX / 密码段 session 等）
为每个账号自动分配独立随机 Session ID，实现「一号一独立 IP」，彻底避免批量并发时的 IP 关联与风控。
"""
from __future__ import annotations

import random
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def new_proxy_session_id(length: int = 8) -> str:
    """生成随机唯一的会话 session ID（保证每个账号一号一 IP，不撞车）。"""
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choices(chars, k=length))


def normalize_proxy_url(proxy: str) -> str:
    """标准化代理 URL：支持 user:pass@host:port, host:port:user:pass 等。"""
    proxy = str(proxy or "").strip()
    if not proxy or proxy.startswith("#"):
        return ""
    if "://" in proxy:
        return proxy
    if "@" in proxy:
        return f"http://{proxy}"

    # user:pass:host:port
    raw_parts = proxy.rsplit(":", 2)
    if len(raw_parts) == 3 and raw_parts[2].isdigit() and ":" in raw_parts[0]:
        credentials, host, port = raw_parts
        username, password = credentials.split(":", 1)
        if username and password and host:
            return f"http://{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}:{port}"

    # host:port:user:pass
    parts = proxy.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in proxy:
        host, port, username, password = parts
        return f"http://{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}:{port}"

    return f"http://{proxy}"


def proxy_url_with_credentials(parsed: Any, username: str, password: str) -> str:
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def route_proxy_for_worker(proxy: str, session_id: str = "") -> str:
    """为单次 Worker 任务重写动态住宅代理的会话 ID，保证一号一独立 IP。"""
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return ""
    if not session_id:
        session_id = new_proxy_session_id(8)

    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not username and not password:
        return proxy

    # 1. 用户名重写 (-sid-xxx / -session-xxx / _session-xxx)
    if re.search(r"(?i)(-sid-)[a-z0-9]+", username):
        username = re.sub(r"(?i)(-sid-)[a-z0-9]+", rf"\g<1>{session_id}", username)
    elif re.search(r"(?i)(-session-)[a-z0-9]+", username):
        username = re.sub(r"(?i)(-session-)[a-z0-9]+", rf"\g<1>{session_id}", username)
    elif re.search(r"(?i)(_session-)[a-z0-9]+", username):
        username = re.sub(r"(?i)(_session-)[a-z0-9]+", rf"\g<1>{session_id}", username)

    # 2. 密码重写 (prefix-CC-session-ttl)
    m = re.fullmatch(
        r"(?P<prefix>.+)-(?P<country>[A-Za-z]{2})-(?P<session>\d+)-(?P<ttl>\d+[A-Za-z]+)",
        password,
    )
    if m:
        random_num = str(random.randint(100000, 999999))
        password = f"{m['prefix']}-{m['country']}-{random_num}-{m['ttl']}"

    return proxy_url_with_credentials(parsed, username, password)
