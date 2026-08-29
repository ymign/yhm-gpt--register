"""exporter.py — Sub2API JSON 与多格式导出引擎
=================================================
将处理成功的账号凭证渲染为 Sub2API 标准 JSON、ChatGPT 官方 Session JSON 及各类常用文本。
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone


def decode_jwt_payload(token: str) -> dict:
    """安全解析 JWT Token 的 payload 段。"""
    t = (token or "").strip()
    if not t or t.count(".") < 2:
        return {}
    try:
        part = t.split(".")[1]
        pad = (4 - (len(part) % 4)) % 4
        decoded = base64.urlsafe_b64decode(part + ("=" * pad))
        return json.loads(decoded)
    except Exception:
        return {}


def render_sub2api_json(accounts: list[dict], proxies: list[str] | None = None) -> str:
    """渲染为 Sub2API 标准账号导入 JSON 对象结构。

    结构规范：
    {
      "exported_at": "2026-08-29T07:22:42.738Z",
      "proxies": [],
      "accounts": [
        {
          "name": "email@example.com",
          "platform": "openai",
          "type": "oauth",
          "concurrency": 10,
          "priority": 1,
          "credentials": {
            "access_token": "...",
            "refresh_token": "...",
            "id_token": "...",
            "chatgpt_account_id": "...",
            "chatgpt_user_id": "...",
            "email": "...",
            "expires_at": "...",
            "expires_in": 864000,
            "plan_type": "free"
          },
          "extra": {
            "email": "...",
            "email_key": "...",
            "name": "...",
            "source": "chatgpt_web_session",
            "last_refresh": "..."
          }
        }
      ]
    }
    """
    now = datetime.now(timezone.utc)
    now_iso_ms = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    now_ts = now.timestamp()

    out_accounts = []
    for acc in accounts or []:
        email = str(acc.get("email") or "").strip()
        at = str(acc.get("access_token") or "").strip()
        rt = str(acc.get("refresh_token") or "").strip()
        it = str(acc.get("id_token") or "").strip()
        if not at and not rt:
            continue

        claims = decode_jwt_payload(at) if at else {}
        auth_claims = claims.get("https://api.openai.com/auth") or {}

        chatgpt_account_id = str(
            auth_claims.get("chatgpt_account_id")
            or acc.get("account_id")
            or ""
        ).strip()

        chatgpt_user_id = str(
            auth_claims.get("chatgpt_user_id")
            or auth_claims.get("user_id")
            or claims.get("sub")
            or ""
        ).strip()

        plan_type = str(
            acc.get("plan_type")
            or auth_claims.get("chatgpt_plan_type")
            or auth_claims.get("plan_type")
            or "free"
        ).strip().lower()

        exp = claims.get("exp")
        if exp:
            exp_dt = datetime.fromtimestamp(exp, timezone.utc)
            expires_at = exp_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            expires_in = max(0, int(exp - now_ts))
        else:
            expires_at = ""
            expires_in = 864000

        email_key = email.replace("@", "_").replace(".", "_")

        out_accounts.append({
            "name": email,
            "platform": "openai",
            "type": "oauth",
            "concurrency": 10,
            "priority": 1,
            "credentials": {
                "access_token": at,
                "refresh_token": rt,
                "id_token": it,
                "chatgpt_account_id": chatgpt_account_id,
                "chatgpt_user_id": chatgpt_user_id,
                "email": email,
                "expires_at": expires_at,
                "expires_in": expires_in,
                "plan_type": plan_type,
            },
            "extra": {
                "email": email,
                "email_key": email_key,
                "name": email,
                "source": "chatgpt_web_session",
                "last_refresh": now_iso_ms,
            }
        })

    root = {
        "exported_at": now_iso_ms,
        "proxies": proxies or [],
        "accounts": out_accounts,
    }
    return json.dumps(root, ensure_ascii=False, indent=2)


def render_session_json(accounts: list[dict]) -> str:
    """渲染为 ChatGPT 官方 session 完整数据结构数组。"""
    items = []
    now_ts = time.time()
    for acc in accounts or []:
        email = str(acc.get("email") or "").strip()
        at = str(acc.get("access_token") or "").strip()
        st = str(acc.get("session_token") or "").strip()
        totp_secret = str(acc.get("totp_secret") or "").strip()

        payload = decode_jwt_payload(at) if at else {}
        auth_claims = payload.get("https://api.openai.com/auth") or {}
        user_id = auth_claims.get("user_id") or payload.get("sub") or f"user-{email.split('@')[0] if '@' in email else email}"
        account_id = auth_claims.get("chatgpt_account_id") or ""
        plan_type = auth_claims.get("plan_type") or "free"

        exp = payload.get("exp")
        exp_iso = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if exp else ""

        items.append({
            "user": {
                "id": user_id,
                "name": email.split("@")[0] if "@" in email else email,
                "email": email,
                "image": "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
                "picture": "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
                "idp": "auth0",
                "iat": int(now_ts),
                "mfa": bool(totp_secret),
            },
            "expires": exp_iso,
            "account": {
                "id": account_id,
                "createdTime": now_ts,
                "planType": plan_type,
                "structure": "personal",
            },
            "accessToken": at,
            "authProvider": "openai",
            "sessionToken": st,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def render_lines_email_pwd_2fa(accounts: list[dict]) -> str:
    """邮箱----新密码----2FA 文本格式"""
    lines = []
    for acc in accounts or []:
        em = str(acc.get("email") or "").strip()
        pwd = str(acc.get("new_password") or acc.get("password") or "").strip()
        sec = str(acc.get("totp_secret") or "").strip()
        lines.append(f"{em}----{pwd}----{sec}")
    return "\n".join(lines)


def render_lines_email_pwd(accounts: list[dict]) -> str:
    """邮箱----新密码 文本格式"""
    lines = []
    for acc in accounts or []:
        em = str(acc.get("email") or "").strip()
        pwd = str(acc.get("new_password") or acc.get("password") or "").strip()
        lines.append(f"{em}----{pwd}")
    return "\n".join(lines)


def render_lines_email_at(accounts: list[dict]) -> str:
    """邮箱----AccessToken 文本格式"""
    lines = []
    for acc in accounts or []:
        em = str(acc.get("email") or "").strip()
        at = str(acc.get("access_token") or "").strip()
        if at:
            lines.append(f"{em}----{at}")
    return "\n".join(lines)


def render_lines_at_only(accounts: list[dict]) -> str:
    """纯 AccessToken 列表"""
    lines = []
    for acc in accounts or []:
        at = str(acc.get("access_token") or "").strip()
        if at:
            lines.append(at)
    return "\n".join(lines)


def render_lines_rt_only(accounts: list[dict]) -> str:
    """纯 RefreshToken (RT) 列表"""
    lines = []
    for acc in accounts or []:
        rt = str(acc.get("refresh_token") or "").strip()
        if rt:
            lines.append(rt)
    return "\n".join(lines)
