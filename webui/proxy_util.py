"""代理 URL 处理与国家/会话动态重写路由工具库。

支持常见动态住宅代理格式的国家改写与会话独立（一号一 IP）：
  1. 用户名包含 -region-XX- / -country-XX- / _country-XX / -sid-XXX / -session-XXX 等
  2. 密码包含 prefix-CC-session-ttl
  3. host:port:user:pass 或 user:pass@host:port 等标准化解析
"""
from __future__ import annotations

import random
import re
from typing import Any, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

# 热门高爆与主流国家列表
COUNTRY_OPTIONS = [
    {"code": "", "name": "自动 / 保持原样", "rate": "默认"},
    {"code": "BR", "name": "巴西 (Brazil)", "rate": "Plus试用高爆推荐 ★★★★★", "lang": "pt-BR,pt;q=0.9,en-US;q=0.8"},
    {"code": "DE", "name": "德国 (Germany)", "rate": "欧洲高爆推荐 ★★★★", "lang": "de-DE,de;q=0.9,en-US;q=0.8"},
    {"code": "GB", "name": "英国 (United Kingdom)", "rate": "欧洲高爆推荐 ★★★★", "lang": "en-GB,en;q=0.9,en-US;q=0.8"},
    {"code": "PL", "name": "波兰 (Poland)", "rate": "欧洲推荐 ★★★★", "lang": "pl-PL,pl;q=0.9,en-US;q=0.8"},
    {"code": "ES", "name": "西班牙 (Spain)", "rate": "欧洲推荐 ★★★★", "lang": "es-ES,es;q=0.9,en-US;q=0.8"},
    {"code": "AR", "name": "阿根廷 (Argentina)", "rate": "拉美推荐 ★★★★", "lang": "es-AR,es;q=0.9,en-US;q=0.8"},
    {"code": "US", "name": "美国 (United States)", "rate": "经典通用 ★★★", "lang": "en-US,en;q=0.9"},
    {"code": "JP", "name": "日本 (Japan)", "rate": "亚太通用 ★★", "lang": "ja-JP,ja;q=0.9,en-US;q=0.8"},
]

COUNTRY_LANG_MAP = {c["code"]: c.get("lang", "en-US,en;q=0.9") for c in COUNTRY_OPTIONS if c["code"]}


def new_proxy_session_id(length: int = 8) -> str:
    """生成随机的会话 session ID（保证每个账号一号一 IP，不撞车）。"""
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choices(chars, k=length))


def normalize_proxy_url(proxy: str) -> str:
    """标准化代理 URL：支持 user:pass@host:port, user:pass:host:port, host:port:user:pass, socks5h:// 等。"""
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
            return (
                f"http://{quote(username, safe='-._~')}:"
                f"{quote(password, safe='-._~')}@{host}:{port}"
            )

    # host:port:user:pass
    parts = proxy.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in proxy:
        host, port, username, password = parts
        return (
            f"http://{quote(username, safe='-._~')}:"
            f"{quote(password, safe='-._~')}@{host}:{port}"
        )
    return f"http://{proxy}"


def proxy_url_with_credentials(parsed: Any, username: str, password: str) -> str:
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def route_proxy_country(proxy: str, country: str = "", session_id: str = "") -> str:
    """智能重写动态住宅代理的国家与会话 ID。

    支持常见代理商协议：
      1. 用户名中包含 -region-XX- / -country-XX- / _country-XX / -sid-XXX / -session-XXX / _session-XXX
      2. 密码中包含 prefix-CC-session-ttl
    """
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return proxy
    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not parsed.hostname:
        return proxy

    sid = session_id or new_proxy_session_id()
    cc = (country or "").strip().upper()

    changed_user = False
    new_username = username

    # 1. 检查 username 是否包含国家或 session 模式
    if username:
        if cc:
            # 替换 -region-XX / -country-XX
            new_u, n1 = re.subn(r"(?i)(-region-)[a-z]{2}\b", rf"\g<1>{cc}", new_username)
            if n1 > 0:
                new_username = new_u
                changed_user = True
            new_u, n2 = re.subn(r"(?i)(-country-)[a-z]{2}\b", rf"\g<1>{cc}", new_username)
            if n2 > 0:
                new_username = new_u
                changed_user = True
            new_u, n3 = re.subn(r"(?i)(_country-)[a-z]{2}\b", rf"\g<1>{cc.lower()}", new_username)
            if n3 > 0:
                new_username = new_u
                changed_user = True

        if sid:
            new_u, n4 = re.subn(r"(?i)(-sid-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n4 > 0:
                new_username = new_u
                changed_user = True
            new_u, n5 = re.subn(r"(?i)(-session-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n5 > 0:
                new_username = new_u
                changed_user = True
            new_u, n6 = re.subn(r"(?i)(_session-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n6 > 0:
                new_username = new_u
                changed_user = True

    # 2. 检查 password 是否符合 prefix-CC-session-ttl
    changed_pass = False
    new_password = password
    if password:
        match = re.fullmatch(
            r"(?P<prefix>.+)-(?P<country>[A-Za-z]{2})-(?P<session>\d+)-(?P<ttl>\d+[A-Za-z]+)",
            password,
        )
        if match:
            routed_country = cc or match.group("country")
            routed_sid = sid if sid.isdigit() else str(random.randint(10_000_000, 99_999_999))
            new_password = (
                f"{match.group('prefix')}-{routed_country.upper()}-"
                f"{routed_sid}-{match.group('ttl')}"
            )
            changed_pass = True

    if changed_user or changed_pass:
        return proxy_url_with_credentials(parsed, new_username, new_password)
    return proxy
