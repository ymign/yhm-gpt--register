"""重跑 OAuth 导出与凭证生成任务管理器 (Codex OAuth / CPA / Sub2API)。

核心功能：
  1. 对指定账号执行 Codex OAuth 直连重登与 Token 刷新（获取 access_token + refresh_token + id_token）
  2. 遇到手机验证（add-phone）直接标记失败/跳过，绝不阻塞接码或扣费
  3. 支持邮箱 OTP 自动取码推进登录态
  4. 成功后回写 access_token、refresh_token 到 SQLite 数据库
  5. 自动生成并持久化 CPA 格式 JSON 与 Sub2API 标准格式 JSON，支持一键打包与单独下载
  6. 多 Worker 线程池并发执行，支持取消 / 停止，SSE 实时广播进度与细分日志
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import queue
import re
import secrets
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from config import Config
from fingerprint import generate_fingerprint
from http_client import create_http_session
from mail_providers import create_mail_provider, get_provider_class
from sentinel import get_sentinel_token
from . import db
from .proxy_util import COUNTRY_LANG_MAP, new_proxy_session_id, resolve_target_country, route_proxy_country

logger = logging.getLogger(__name__)

EXPORTS_DIR = Path(__file__).resolve().parent / "exports"
CPA_DIR = EXPORTS_DIR / "cpa"
SUB2_DIR = EXPORTS_DIR / "sub2api"
CPA_DIR.mkdir(parents=True, exist_ok=True)
SUB2_DIR.mkdir(parents=True, exist_ok=True)


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _totp_now(secret_b32: str) -> str:
    """计算当前 30s 窗口的 6 位 TOTP 码。"""
    try:
        clean_secret = re.sub(r"[^A-Za-z2-7]", "", str(secret_b32 or "")).upper()
        if not clean_secret:
            return ""
        key = base64.b32decode(clean_secret + "=" * (-len(clean_secret) % 8))
        counter = int(time.time()) // 30
        msg = struct.pack(">Q", counter)
        import hmac
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = h[-1] & 0x0F
        code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000
        return str(code).zfill(6)
    except Exception:
        return ""


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _extract_workspace_id_from_session(session, html_text: str = "") -> str:
    """从 session cookie 或 HTML 中全面提取 workspace_id / account_id。"""
    try:
        candidate_cookies = []
        for c in getattr(session.cookies, "jar", []):
            val = getattr(c, "value", "")
            if val and "." in val:
                candidate_cookies.append(val)
        auth_session = session.cookies.get("oai-client-auth-session", "")
        if auth_session:
            candidate_cookies.insert(0, auth_session)

        for cookie_val in candidate_cookies:
            parts = cookie_val.split(".")
            for segment in parts:
                seg = (segment or "").strip()
                if len(seg) < 8:
                    continue
                try:
                    padded = seg + "=" * ((4 - len(seg) % 4) % 4)
                    decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace"))
                    if isinstance(decoded, dict):
                        # 直接字段
                        for k in ("workspace_id", "workspaceId", "chatgpt_account_id", "account_id", "org_id"):
                            v = str(decoded.get(k) or "").strip()
                            if v and len(v) >= 16:
                                return v
                        # 嵌套 auth 字段
                        auth_obj = decoded.get("https://api.openai.com/auth")
                        if isinstance(auth_obj, dict):
                            for k in ("chatgpt_account_id", "account_id", "workspace_id"):
                                v = str(auth_obj.get(k) or "").strip()
                                if v and len(v) >= 16:
                                    return v
                        # workspaces 数组
                        workspaces = decoded.get("workspaces", [])
                        if isinstance(workspaces, list) and workspaces:
                            for it in workspaces:
                                if isinstance(it, dict):
                                    wid = (it.get("id", "") or it.get("workspace_id", "") or "").strip()
                                    if wid and len(wid) >= 16:
                                        return wid
                except Exception:
                    pass
    except Exception:
        pass

    if html_text:
        try:
            text = html_text.replace('\\"', '"')
            patterns = [
                r'workspaces".{0,1600}?"id"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'workspaces".{0,1600}?"id","([0-9a-fA-F-]{36})"',
                r'"workspace_id"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'"workspaceId"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'"chatgpt_account_id"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'"account_id"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'["\']workspace_id["\']\s*[:=]\s*["\']([0-9a-fA-F-]{36})["\']',
                r'name="workspace_id"\s*value="([0-9a-fA-F-]{36})"',
                r'data-workspace-id="([0-9a-fA-F-]{36})"',
            ]
            for p in patterns:
                m = re.search(p, text, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    res = (m.group(1) if m.groups() else m.group(0)).strip()
                    if res and len(res) >= 16:
                        return res
        except Exception:
            pass
    return ""


def _callback_has_code(url: str, redirect_uri: str) -> bool:
    if not url:
        return False
    try:
        if "code=" in url and ("localhost:1455" in url or "127.0.0.1" in url or (redirect_uri and redirect_uri.split("?")[0] in url)):
            return True
        cb_base = (redirect_uri or "").split("?", 1)[0].rstrip("/")
        target = url.split("?", 1)[0].rstrip("/")
        if cb_base and target == cb_base:
            qs = parse_qs(urlparse(url).query)
            return bool((qs.get("code", [""])[0] or "").strip())
    except Exception:
        pass
    return False


def _handle_choose_account_page(session, html_text: str, current_url: str, post_headers: dict) -> str:
    """处理 /choose-an-account 多账号选择页。"""
    m = re.search(r"us_[A-Za-z0-9]{16,}", html_text or "")
    if not m:
        return ""
    session_id = m.group(0)
    headers = dict(post_headers)
    headers["Origin"] = "https://auth.openai.com"
    headers["Referer"] = "https://auth.openai.com/choose-an-account"
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    try:
        resp = session.post(
            "https://auth.openai.com/api/accounts/session/select",
            headers=headers,
            json={"session_id": session_id},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            return (data.get("continue_url") or data.get("redirect_url") or "").strip()
    except Exception:
        pass
    return ""


def _short_url(url: str, limit: int = 90) -> str:
    u = (url or "").strip()
    if len(u) <= limit:
        return u
    return u[:limit] + "..."


def _page_kind_from_url(url: str) -> str:
    u = (url or "").lower()
    if "localhost:1455/auth/callback" in u or "code=" in u:
        return "callback"
    if "/log-in/password" in u:
        return "密码页"
    if "/email-verification" in u:
        return "邮箱验证页"
    if "/mfa-challenge" in u:
        return "2FA页"
    if "/add-phone" in u or "/phone-verification" in u:
        return "绑手机页"
    if "/workspace" in u:
        return "工作空间页"
    if "/consent" in u or "/sign-in-with-chatgpt" in u:
        return "授权同意页"
    if "/choose-an-account" in u:
        return "选账号页"
    if "/log-in" in u:
        return "登录页"
    if "/oauth/authorize" in u or "/oauth2/auth" in u:
        return "OAuth授权入口"
    if "/accounts/login" in u:
        return "登录challenge"
    return "页面"


def _is_real_workspace_url(url: str) -> bool:
    u = (url or "").lower()
    if "/workspace" in u or "/sign-in-with-chatgpt/" in u:
        return True
    if "/consent" in u and "auth.openai.com" in u:
        return True
    return False


def _json_or_empty(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_auth_page(data: Any) -> tuple[str, str, str]:
    """从 authorize/continue 一类 JSON 抽出 page_type / continue_url / email_verification_mode。"""
    if not isinstance(data, dict):
        return "", "", ""
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    page_type = str((page or {}).get("type") or "").strip().lower()
    continue_url = str(data.get("continue_url") or data.get("redirect_url") or "").strip()
    payload = (page or {}).get("payload") if isinstance((page or {}).get("payload"), dict) else {}
    mode = str((payload or {}).get("email_verification_mode") or "").strip().lower()
    return page_type, continue_url, mode


def _describe_auth_page(page_type: str, continue_url: str, mode: str = "") -> str:
    bits = [f"page_type={page_type or '(空)'}"]
    if mode:
        bits.append(f"mode={mode}")
    if continue_url:
        bits.append(f"continue={_page_kind_from_url(continue_url)} {_short_url(continue_url)}")
    else:
        bits.append("continue=无")
    return " ".join(bits)


def _is_password_page(page_type: str, continue_url: str) -> bool:
    return (page_type or "") == "login_password" or "/log-in/password" in (continue_url or "")


def _is_otp_page(page_type: str, continue_url: str, mode: str = "") -> bool:
    if (page_type or "") in ("email_otp_verification", "passwordless_signup", "passwordless_login"):
        return True
    if "/email-verification" in (continue_url or ""):
        return True
    if (mode or "") in ("passwordless_signup", "passwordless_login", "email_otp_verification"):
        return True
    return False


def _is_mfa_page(page_type: str, continue_url: str) -> bool:
    return (page_type or "") in ("mfa_challenge", "totp_verification") or "/mfa-challenge" in (continue_url or "")


def _still_needs_login(page_type: str, continue_url: str) -> bool:
    pt = (page_type or "").strip().lower()
    if pt in ("login", "log_in", "login_password"):
        return True
    return _page_kind_from_url(continue_url) in ("登录页", "密码页")


def _looks_logged_in(page_type: str, continue_url: str, mode: str = "") -> bool:
    """只有明确离开登录/OTP/2FA 才算已登录。空 page + 空 continue 不算。"""
    if _still_needs_login(page_type, continue_url):
        return False
    if _is_otp_page(page_type, continue_url, mode):
        return False
    if _is_mfa_page(page_type, continue_url):
        return False
    if continue_url:
        return True
    pt = (page_type or "").strip().lower()
    return pt in ("add_phone", "workspace_select", "consent")


def _kickoff_login_email_otp(session, post_headers: dict, timeout, log_fn) -> tuple[bool, str, str, str]:
    """停在密码页且无密码时，向 OpenAI 申请发邮箱 OTP。

    当前还在密码页，通常还没有 email-otp challenge，必须先 send 再建码。
    resend 放最后，避免在没 challenge 时空转。
    返回 (成功, page_type, continue_url, mode)。
    """
    attempts = [
        ("GET", "https://auth.openai.com/api/accounts/email-otp/send", "https://auth.openai.com/log-in/password"),
        ("POST", "https://auth.openai.com/api/accounts/passwordless/send-otp", "https://auth.openai.com/log-in/password"),
        ("POST", "https://auth.openai.com/api/accounts/password/send-otp", "https://auth.openai.com/log-in/password"),
        ("POST", "https://auth.openai.com/api/accounts/email-otp/resend", "https://auth.openai.com/email-verification"),
    ]
    for method, url, referer in attempts:
        headers = dict(post_headers)
        headers["Referer"] = referer
        name = url.rsplit("/", 1)[-1]
        try:
            if method == "GET":
                resp = session.get(url, headers=headers, timeout=timeout)
            else:
                resp = session.post(url, headers=headers, json={}, timeout=timeout)
        except Exception as e:
            if log_fn:
                log_fn(f"[3/6] 申请发码 {method} {name} 异常: {e}")
            continue
        snip = (resp.text or "").replace("\n", " ")[:120]
        if log_fn:
            log_fn(f"[3/6] 申请发码 {method} {name} HTTP {resp.status_code} {snip}")
        if resp.status_code == 200:
            pt, cu, md = _parse_auth_page(_json_or_empty(resp))
            if log_fn:
                log_fn(f"[3/6] 发码成功 {method} {name} {_describe_auth_page(pt, cu, md)}")
            return True, pt, cu, md
    if log_fn:
        log_fn("[3/6] 申请发码全部失败")
    return False, "", "", ""


def _submit_totp_if_needed(
    session,
    post_headers: dict,
    page_type: str,
    continue_url: str,
    totp_secret: str,
    timeout,
    log_fn,
    verify_mode: str = "",
) -> tuple[str, str, str]:
    """当前是 2FA 页才提交 TOTP；否则原样返回。失败直接抛错，不假装通过。"""
    if not _is_mfa_page(page_type, continue_url):
        return page_type, continue_url, verify_mode or ""
    verify_mode = verify_mode or ""
    if not (totp_secret or "").strip():
        raise RuntimeError("停在 2FA 页，但库里没有 totp_secret，无法提交动态码")
    challenge_id = continue_url.split("/")[-1] if "/mfa-challenge/" in (continue_url or "") else ""
    totp_code = _totp_now(totp_secret)
    if not totp_code:
        raise RuntimeError("库里有 totp_secret，但当前窗口无法算出 6 位动态码")
    if log_fn:
        log_fn(f"[2/6] 当前是 2FA 页，提交 TOTP={totp_code} challenge_id={challenge_id[:8] or '无'}")
    mfa_headers = dict(post_headers)
    mfa_headers["Referer"] = (
        f"https://auth.openai.com/mfa-challenge/{challenge_id}" if challenge_id else "https://auth.openai.com/mfa-challenge"
    )
    mfa_resp = session.post(
        "https://auth.openai.com/api/accounts/mfa/verify",
        headers=mfa_headers,
        json={"code": totp_code, "type": "totp", "id": challenge_id},
        allow_redirects=False,
        timeout=timeout,
    )
    step_data = _json_or_empty(mfa_resp)
    page_type, continue_url, next_mode = _parse_auth_page(step_data)
    verify_mode = next_mode or verify_mode
    if log_fn:
        log_fn(f"[2/6] 2FA 验证 HTTP {mfa_resp.status_code} {_describe_auth_page(page_type, continue_url, verify_mode)}")
    if mfa_resp.status_code != 200:
        raise RuntimeError(f"2FA 验证失败: HTTP {mfa_resp.status_code} - {(mfa_resp.text or '')[:180]}")
    if _is_password_page(page_type, continue_url):
        if log_fn:
            log_fn("[2/6] 2FA 后仍回到密码页，未视为已登录")
    return page_type, continue_url, verify_mode


def _abs_auth_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("/"):
        return urljoin("https://auth.openai.com", u)
    return u


def _enter_auth_page(session, url: str, nav_headers: dict, timeout, log_fn, label: str) -> str:
    """GET continue_url，把服务端返回的下一页真正打开，绑定登录会话。"""
    curr = _abs_auth_url(url)
    if not curr:
        return ""
    for hop in range(6):
        try:
            r = session.get(curr, headers=nav_headers, allow_redirects=False, timeout=timeout)
        except Exception as e:
            if log_fn:
                log_fn(f"{label} GET 异常: {e}")
            return curr
        loc = (r.headers.get("Location") or r.headers.get("location") or "").strip()
        kind = _page_kind_from_url(loc or curr)
        if log_fn:
            log_fn(f"{label} GET hop {hop + 1} HTTP {r.status_code} {kind} {_short_url(loc or curr)}")
        if loc:
            loc = _abs_auth_url(loc)
            if _page_kind_from_url(loc) in ("登录页", "密码页"):
                if log_fn:
                    log_fn(f"{label} 打开后又回到登录/密码页，会话没保住")
                return loc
            curr = loc
            continue
        return curr
    return curr


def _follow_oauth_callback(
    session,
    start_urls: list[str],
    redirect_uri: str,
    nav_headers: dict,
    post_headers: dict,
    device_id: str,
    timeout: int = 30,
    log_fn: Optional[Callable[[str], None]] = None,
    fallback_workspace_id: str = "",
) -> str:
    """全自动跟踪 OAuth 重定向链，智能处理 302 重定向、workspace_select、choose-an-account 等中间步骤，直达 callback_url。"""
    def _log(msg: str):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    for start_idx, start_url in enumerate(start_urls):
        if not start_url:
            continue
        curr = start_url
        if curr.startswith("/"):
            curr = "https://auth.openai.com" + curr

        start_kind = _page_kind_from_url(curr)
        if start_kind in ("密码页", "登录页"):
            _log(f"[5/6] 跳过起点 {start_idx+1}/{len(start_urls)}：这是{start_kind}，未登录跟下去也拿不到 callback code  {_short_url(curr)}")
            continue

        _log(f"[5/6] 跟踪跳转 {start_idx+1}/{len(start_urls)} 起点={start_kind} {_short_url(curr)}")

        for hop in range(15):
            if _callback_has_code(curr, redirect_uri):
                _log(f"[5/6] 已捕获 callback code: {_short_url(curr)}")
                return curr

            r = None
            for retry_idx in range(3):
                try:
                    r = session.get(curr, headers=nav_headers, allow_redirects=False, timeout=timeout)
                    break
                except Exception as e:
                    if retry_idx < 2:
                        _log(f"[5/6] 链路网络波动 ({e})，正在自动重试第 {retry_idx+1}/2 次...")
                        time.sleep(1.5)
                    else:
                        _log(f"[5/6] 链路请求异常: {e}")
            if r is None:
                break

            status = r.status_code
            loc = (r.headers.get("Location") or r.headers.get("location") or "").strip()
            kind = _page_kind_from_url(loc or curr)
            if loc:
                loc_full = loc if not loc.startswith("/") else urljoin("https://auth.openai.com", loc)
                _log(f"[5/6] hop {hop+1}: HTTP {status} {kind} -> {_short_url(loc_full)}")
            else:
                _log(f"[5/6] hop {hop+1}: HTTP {status} 停在{kind} {_short_url(curr)}")

            if loc:
                if loc.startswith("/"):
                    loc = urljoin("https://auth.openai.com", loc)
                if _callback_has_code(loc, redirect_uri):
                    _log(f"[5/6] 重定向中捕获 callback code: {_short_url(loc)}")
                    return loc
                if _page_kind_from_url(loc) in ("密码页", "登录页") and hop >= 4:
                    _log("[5/6] 跳转落到登录/密码页，说明当前会话未真正登录，停止这条起点")
                    break
                curr = loc
                continue

            # 处理 HTTP 200 页面
            if status == 200:
                html_text = r.text or ""

                if _callback_has_code(curr, redirect_uri):
                    return curr

                if _page_kind_from_url(curr) in ("密码页", "登录页") and hop >= 4:
                    _log("[5/6] 当前仍是登录/密码页，未进入工作空间，停止这条起点（不会把登录页当成 workspace）")
                    break

                # 1. 检查 HTML 中是否有 meta refresh 或 JS location 重定向
                m_meta = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\']+)["\']', html_text, re.IGNORECASE)
                if m_meta:
                    meta_target = m_meta.group(1).strip()
                    if meta_target.startswith("/"):
                        meta_target = urljoin("https://auth.openai.com", meta_target)
                    _log(f"[5/6] HTML meta refresh -> {_short_url(meta_target)}")
                    if _callback_has_code(meta_target, redirect_uri):
                        return meta_target
                    curr = meta_target
                    continue

                m_loc = re.search(r'(?:window\.location(?:\.href|\.replace)?|location\.href)\s*=\s*["\']([^"\']+)["\']', html_text)
                if m_loc:
                    loc_target = m_loc.group(1).strip()
                    if loc_target.startswith("/"):
                        loc_target = urljoin("https://auth.openai.com", loc_target)
                    _log(f"[5/6] HTML JS location -> {_short_url(loc_target)}")
                    if _callback_has_code(loc_target, redirect_uri):
                        return loc_target
                    curr = loc_target
                    continue

                # 2. 仅在真实 workspace / consent URL 上提交选择，不用 HTML 里的 workspace 字样误判
                is_workspace_like = _is_real_workspace_url(curr)
                if is_workspace_like:
                    wid = _extract_workspace_id_from_session(session, html_text) or fallback_workspace_id
                    if wid:
                        _log(f"[5/6] 工作空间页，提交 workspace/select id={wid[:8]}...")
                    else:
                        _log("[5/6] 工作空间页，但没解析到 workspace_id，改空 payload 试一次")
                    ws_headers = dict(post_headers)
                    ws_headers["Origin"] = "https://auth.openai.com"
                    ws_headers["Referer"] = curr
                    ws_headers["Content-Type"] = "application/json"
                    if device_id:
                        ws_headers["oai-device-id"] = device_id
                    try:
                        payload = {"workspace_id": wid} if wid else {}
                        ws_resp = None
                        for ws_try in range(3):
                            try:
                                ws_resp = session.post(
                                    "https://auth.openai.com/api/accounts/workspace/select",
                                    headers=ws_headers,
                                    json=payload,
                                    allow_redirects=False,
                                    timeout=timeout,
                                )
                                break
                            except Exception as ws_err:
                                if ws_try < 2:
                                    time.sleep(1.5)
                                else:
                                    _log(f"[5/6] workspace/select 网络异常: {ws_err}")
                        if ws_resp is None:
                            break
                        ws_loc = (ws_resp.headers.get("Location") or ws_resp.headers.get("location") or "").strip()
                        _log(f"[5/6] workspace/select HTTP {ws_resp.status_code} loc={_short_url(ws_loc) or '无'}")
                        if ws_loc:
                            if ws_loc.startswith("/"):
                                ws_loc = urljoin("https://auth.openai.com", ws_loc)
                            if _callback_has_code(ws_loc, redirect_uri):
                                return ws_loc
                            curr = ws_loc
                            continue
                        ws_data = {}
                        try:
                            ws_data = ws_resp.json() if ws_resp.status_code == 200 else {}
                        except Exception:
                            ws_data = {}
                        next_url = (ws_data.get("continue_url") or ws_data.get("redirect_url") or "").strip()
                        if next_url:
                            if next_url.startswith("/"):
                                next_url = urljoin("https://auth.openai.com", next_url)
                            _log(f"[5/6] workspace/select continue_url={_short_url(next_url)}")
                            if _callback_has_code(next_url, redirect_uri):
                                return next_url
                            curr = next_url
                            continue
                        _log(f"[5/6] workspace/select 无下一跳 body={(ws_resp.text or '')[:120]}")
                    except Exception as e:
                        _log(f"[5/6] workspace/select 异常: {e}")

                # 3. 检查是否是 /choose-an-account 页面
                if "/choose-an-account" in curr or "choose-an-account" in html_text:
                    next_url = _handle_choose_account_page(session, html_text, curr, post_headers)
                    if next_url:
                        if next_url.startswith("/"):
                            next_url = urljoin("https://auth.openai.com", next_url)
                        if _callback_has_code(next_url, redirect_uri):
                            return next_url
                        curr = next_url
                        continue

                # 4. HTML 正则提取
                m_code = re.search(r"http://localhost:1455/auth/callback\?[^\s\"'<>]+", html_text)
                if m_code:
                    return m_code.group(0)

                m_code_only = re.search(r"[?&]code=([A-Za-z0-9_\-\.]{20,})", html_text)
                if m_code_only:
                    return f"http://localhost:1455/auth/callback?code={m_code_only.group(1)}"

                break

    return ""


def _get_account_claims(access_token: str) -> dict:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get("https://api.openai.com/auth") or {}
    profile = payload.get("https://api.openai.com/profile") or {}
    exp = payload.get("exp")
    exp_iso = None
    if isinstance(exp, (int, float)):
        exp_iso = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "email": profile.get("email") or "",
        "name": profile.get("name") or "",
        "user_id": auth.get("chatgpt_user_id") or auth.get("user_id") or "",
        "account_id": auth.get("chatgpt_account_id") or "",
        "plan_type": auth.get("chatgpt_plan_type") or "",
        "exp": exp,
        "exp_iso": exp_iso,
    }


def cpa_credential_to_sub2_account(cpa: dict) -> dict:
    """将单个 CPA/Codex 凭证转换为 Sub2API accounts[] 元素。"""
    email = str(cpa.get("email") or "").strip()
    access_token = str(cpa.get("access_token") or "").strip()
    refresh_token = str(cpa.get("refresh_token") or "").strip()
    id_token = str(cpa.get("id_token") or "").strip()
    account_id = str(cpa.get("account_id") or cpa.get("chatgpt_account_id") or "").strip()
    plan_type = str(cpa.get("plan_type") or cpa.get("chatgpt_plan_type") or "").strip()
    exp_iso = str(cpa.get("expired") or cpa.get("expires_at") or "").strip()

    creds: dict[str, Any] = {"access_token": access_token}
    if refresh_token:
        creds["refresh_token"] = refresh_token
    if id_token:
        creds["id_token"] = id_token
    if email:
        creds["email"] = email
    if exp_iso:
        creds["expires_at"] = exp_iso
    if account_id:
        creds["chatgpt_account_id"] = account_id
    if plan_type:
        creds["plan_type"] = plan_type

    return {
        "name": email or "openai-oauth",
        "platform": "openai",
        "type": "oauth",
        "credentials": creds,
        "concurrency": 0,
        "priority": 0,
    }


def build_sub2api_payload(cpa_list: list[dict]) -> dict:
    """多条 CPA 凭证打包生成标准的 sub2api-data 导入格式。"""
    accounts = [cpa_credential_to_sub2_account(c) for c in cpa_list if c and c.get("access_token")]
    return {
        "type": "sub2api-data",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxies": [],
        "accounts": accounts,
    }


class OAuthExportTask:
    """单个 OAuth 导出与凭证重跑任务。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.started_at = time.time()
        self.finished_at = 0.0

        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
                "step": "pending",
                "step_text": "待处理",
                "result": None,
                "started_at": 0.0,
                "finished_at": 0.0,
                "elapsed": 0.0,
                "logs": [],
                "cpa": None,
                "sub2api": None,
            }
            for e in emails
        }
        self.queue: queue.Queue = queue.Queue()
        self.cancelled = False
        self.done_count = 0
        self.stats = {
            "success": 0,
            "need_phone": 0,
            "error": 0,
            "token_invalid": 0,
        }
        self._lock = threading.Lock()

    def next_proxy(self) -> str:
        if not self.proxies:
            return ""
        with self._idx_lock:
            p = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
            return p

    def add_email_log(self, email: str, line: str) -> None:
        ts_str = time.strftime("%H:%M:%S")
        formatted = f"{ts_str} {line}"
        with self._lock:
            if email in self.items:
                self.items[email]["logs"].append(formatted)
                if len(self.items[email]["logs"]) > 200:
                    self.items[email]["logs"] = self.items[email]["logs"][-200:]
        try:
            self.queue.put({"kind": "log", "email": email, "line": f"[{email}] {line}"})
        except Exception:
            pass

    def set_running(self, email: str, step_text: str = "[1/6] 建立会话") -> None:
        now = time.time()
        with self._lock:
            if email in self.items:
                self.items[email]["status"] = "running"
                self.items[email]["step"] = "init"
                self.items[email]["step_text"] = step_text
                self.items[email]["started_at"] = now
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step": "init",
            "step_text": step_text,
            "started_at": now,
        })

    def set_step(self, email: str, step: str, step_text: str) -> None:
        st_at = 0.0
        with self._lock:
            if email in self.items:
                self.items[email]["step"] = step
                self.items[email]["step_text"] = step_text
                st_at = self.items[email].get("started_at") or 0.0
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step": step,
            "step_text": step_text,
            "started_at": st_at,
        })

    def mark_done(self, email: str, result: dict) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            st = result.get("status") or "error"
            if st in self.stats:
                self.stats[st] += 1
            else:
                self.stats["error"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "done"
                it["result"] = result
                it["cpa"] = result.get("cpa")
                it["sub2api"] = result.get("sub2api")
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)
                it["step_text"] = result.get("label") or "完成"

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "result": result,
            "step_text": result.get("label") or "完成",
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_tasks: dict[str, OAuthExportTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _trace_put(trace: Optional[dict], **kwargs) -> None:
    if not isinstance(trace, dict):
        return
    for k, v in kwargs.items():
        if v is None:
            continue
        trace[k] = v


def _ua_family(ua: str, impersonate: str = "", browser_type: str = "") -> str:
    raw = f"{impersonate} {browser_type} {ua}".lower()
    if "safari" in raw or "ios" in raw:
        return "safari"
    if "firefox" in raw:
        return "firefox"
    if "chrome" in raw or "chromium" in raw:
        return "chrome"
    return (browser_type or impersonate or "unknown")[:32]


def _proxy_host_of(proxy: str) -> str:
    p = (proxy or "").strip()
    if not p:
        return ""
    try:
        if "://" not in p:
            p = "http://" + p
        host = urlparse(p).hostname or ""
        return host
    except Exception:
        return p.split("@")[-1].split(":")[0]


def _phone_prefix(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if not digits:
        return ""
    return ("+" + digits)[:7]


def _classify_oauth_error(err: str, status: str = "") -> str:
    s = (err or "").lower()
    st = (status or "").lower()
    if st == "need_phone" or "需接码" in (err or "") or "add_phone" in s:
        return "need_phone"
    if "session is no longer valid" in s or "invalid_state" in s:
        return "session_expired"
    if "no_numbers" in s:
        return "sms_no_numbers"
    if "suspicious behavior from phone" in s:
        return "phone_rejected"
    if "接码" in (err or "") or "sms" in s or "短信" in (err or ""):
        return "sms_fail"
    if "otp" in s or "验证码" in (err or "") or "取件凭证" in (err or ""):
        return "otp_fail"
    if "password" in s or "密码" in (err or ""):
        return "password_fail"
    if "callback" in s or "authorization code" in s:
        return "callback_fail"
    if "token" in s:
        return "token_fail"
    if st in ("cancelled", "not_found"):
        return st
    return "other"


def execute_codex_oauth_flow(
    email: str,
    mail_provider: Any,
    proxy: str = "",
    target_country: str = "",
    account_info: Optional[dict] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    step_fn: Optional[Callable[[str, str], None]] = None,
    skip_sms: bool = True,
    timeout: float = 45.0,
    trace: Optional[dict] = None,
) -> dict:
    """独立且纯净的 Codex OAuth 授权与 Token 获取协议流。

    1. 动态生成 PKCE、State 与 Authorize URL
    2. 使用 create_http_session 模拟真实 TLS 指纹并建立会话 Cookie (oai-client-auth-session)
    3. 提交邮箱 (authorize/continue) 触发服务端自动下发 OTP 邮件
    4. 自动等待并收取邮箱 OTP 验证码，提交 email-otp/validate
    5. 若检测到需手机号验证（add-phone）：skip_sms=True 时直接安全跳过并标记 need_phone
    6. 从 oai-client-auth-session 中提取 workspace_id，POST workspace/select 获取 callback code
    7. POST /oauth/token 交换 access_token, refresh_token, id_token
    """
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    def _step(step_key: str, step_text: str):
        if step_fn:
            step_fn(step_key, step_text)

    account_info = account_info or {}
    device_id = str(account_info.get("device_id") or "").strip() or str(uuid.uuid4())
    trace = trace if isinstance(trace, dict) else {}
    path_steps: list[str] = []
    phone_verified = False
    verified_phone = ""

    country_code = (target_country or account_info.get("reg_country") or "JP").strip().upper()
    lang_full = COUNTRY_LANG_MAP.get(country_code, "ja-JP,ja;q=0.9,en-US;q=0.8" if country_code == "JP" else "en-US,en;q=0.9")

    # 生成与目标国家对齐的一致性浏览器指纹
    fp = generate_fingerprint(country_code=country_code)
    ua = fp.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    impersonate = fp.get("impersonate") or "chrome136"
    _trace_put(
        trace,
        impersonate=impersonate,
        browser_type=fp.get("browser_type") or "",
        ua_family=_ua_family(ua, impersonate, str(fp.get("browser_type") or "")),
        screen=fp.get("screen") or "",
        timezone=fp.get("timezone") or "",
        lang=lang_full,
        proxy_country=country_code,
        proxy_host=_proxy_host_of(proxy),
        has_password=bool(str(account_info.get("password") or "").strip()),
        has_totp=bool(str(account_info.get("totp_secret") or "").strip()),
    )

    client_id = "app_EMoamEEZ73f0CkXaXp7hrann"
    redirect_uri = "http://localhost:1455/auth/callback"
    scope = "openid email profile offline_access"
    state = _b64url_no_pad(secrets.token_bytes(24))
    code_verifier = _b64url_no_pad(secrets.token_bytes(64))
    code_challenge = _b64url_no_pad(hashlib.sha256(code_verifier.encode("utf-8")).digest())

    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "prompt": "login",
    }
    auth_url = f"https://auth.openai.com/oauth/authorize?{urlencode(auth_params)}"

    # 采用自带 _TlsRetrySession 和代理标准化的安全会话
    session = create_http_session(proxy=proxy or None, impersonate=impersonate, user_agent=ua)

    # ──────────────── 阶段 1: 建立会话与预热 ────────────────
    _step("1", "[1/6] 发起鉴权 (建立会话)")
    _log(f"[1/6] 发起 Codex OAuth 鉴权 (模拟 {impersonate}, 国别: {country_code})...")

    # 构造完整导航头（必须包含 client hints 以避免被 Cloudflare 403 拦截）
    nav_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": lang_full,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "User-Agent": ua,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Priority": "u=0, i",
        "Referer": "https://chatgpt.com/",
    }
    if fp.get("sec_ch_ua"):
        nav_headers["Sec-Ch-Ua"] = fp["sec_ch_ua"]
        nav_headers["Sec-Ch-Ua-Mobile"] = fp.get("sec_ch_ua_mobile") or "?0"
        nav_headers["Sec-Ch-Ua-Platform"] = fp["sec_ch_ua_platform"]
        for key, name in (
            ("sec_ch_ua_full_version_list", "Sec-Ch-Ua-Full-Version-List"),
            ("sec_ch_ua_arch", "Sec-Ch-Ua-Arch"),
            ("sec_ch_ua_bitness", "Sec-Ch-Ua-Bitness"),
            ("sec_ch_ua_model", "Sec-Ch-Ua-Model"),
            ("sec_ch_ua_platform_version", "Sec-Ch-Ua-Platform-Version"),
        ):
            if fp.get(key):
                nav_headers[name] = fp[key]

    # 先对 chatgpt.com 进行预热，建立 oai-did 与 Cloudflare 信任态
    try:
        session.get("https://chatgpt.com/", headers=nav_headers, timeout=min(20.0, timeout))
    except Exception as e:
        logger.debug(f"[oauth_export] warmup 提示: {e}")

    try:
        t0 = time.time()
        resp = session.get(auth_url, headers=nav_headers, allow_redirects=True, timeout=timeout)
        t_hop = int((time.time() - t0) * 1000)
        status_code = getattr(resp, "status_code", 0)
        if status_code in (401, 403):
            _log(f"[1/6] ⚠️ 授权服务器响应 {status_code}，正在切换备选会话重试...")
            time.sleep(2)
            session = create_http_session(proxy=proxy or None, impersonate="chrome136", user_agent=ua)
            resp = session.get(auth_url, headers=nav_headers, allow_redirects=True, timeout=timeout)
            status_code = getattr(resp, "status_code", 0)
            if status_code in (401, 403):
                raise RuntimeError(f"OpenAI 授权服务器返回 HTTP {status_code}，当前网络/代理节点受限")
        _log(f"[1/6] 授权会话建立成功 ({t_hop}ms): status={status_code or 'OK'}")
    except Exception as e:
        raise RuntimeError(f"连接 OpenAI 授权服务器失败: {e}")

    # ──────────────── 阶段 2: 提交邮箱 ────────────────
    _step("2", "[2/6] 提交邮箱 (触发发码)")
    _log(f"[2/6] 正在提交账号邮箱: {email} ...")
    otp_issued_after = time.time() - 5

    st_token, so_token = "", ""
    try:
        t_pow0 = time.time()
        st_token, so_token = get_sentinel_token(
            session,
            device_id=device_id,
            flow="authorize_continue",
            user_agent=ua,
            sec_ch_ua=fp.get("sec_ch_ua", ""),
            sec_ch_ua_platform=fp.get("sec_ch_ua_platform", ""),
            sec_ch_ua_mobile=fp.get("sec_ch_ua_mobile", ""),
            sec_ch_ua_full_version_list=fp.get("sec_ch_ua_full_version_list", ""),
            sec_ch_ua_arch=fp.get("sec_ch_ua_arch", ""),
            sec_ch_ua_bitness=fp.get("sec_ch_ua_bitness", ""),
            sec_ch_ua_model=fp.get("sec_ch_ua_model", ""),
            sec_ch_ua_platform_version=fp.get("sec_ch_ua_platform_version", ""),
            screen=fp.get("screen", ""),
            lang=fp.get("lang", ""),
            lang_full=lang_full,
            browser_type=fp.get("browser_type", ""),
            navigator_platform=fp.get("navigator_platform", ""),
            navigator_vendor=fp.get("navigator_vendor"),
            hardware_concurrency=fp.get("hardware_concurrency", 0),
            device_memory=fp.get("device_memory"),
            max_touch_points=fp.get("max_touch_points", 0),
            device_pixel_ratio=fp.get("device_pixel_ratio", 0.0),
            timezone=fp.get("timezone", ""),
        )
        t_pow = int((time.time() - t_pow0) * 1000)
        _log(f"[2/6] Sentinel PoW 计算完成 ({t_pow}ms, token_len={len(st_token)}, so={'有' if so_token else '无'})")
    except Exception as e:
        _log(f"[2/6] Sentinel Token 计算提示: {e}")

    post_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": ua,
        "Accept-Language": lang_full,
        "Origin": "https://auth.openai.com",
        "Referer": "https://auth.openai.com/log-in",
        "oai-device-id": device_id,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if fp.get("sec_ch_ua"):
        post_headers["Sec-Ch-Ua"] = fp["sec_ch_ua"]
        post_headers["Sec-Ch-Ua-Mobile"] = fp.get("sec_ch_ua_mobile") or "?0"
        post_headers["Sec-Ch-Ua-Platform"] = fp["sec_ch_ua_platform"]
    if st_token:
        post_headers["openai-sentinel-token"] = st_token
    if so_token:
        post_headers["openai-sentinel-so-token"] = so_token

    step_resp = session.post(
        "https://auth.openai.com/api/accounts/authorize/continue",
        headers=post_headers,
        json={"username": {"kind": "email", "value": email}, "screen_hint": "login"},
        allow_redirects=False,
        timeout=timeout,
    )
    if step_resp.status_code != 200:
        err_msg = (step_resp.text or "")[:200]
        # 如果遇到 409 invalid_state，自动切换至标准 AuthFlow.run_protocol_login 作为强健兜底
        if step_resp.status_code == 409 or "invalid_state" in err_msg:
            _log(f"[2/6] ⚠️ 触发 409 会话刷新态，正在自动调用 AuthFlow 全链路登录引擎兜底重登...")
            from auth_flow import AuthFlow
            cfg = Config()
            cfg.proxy = proxy or None
            env_overrides = {
                "TARGET_COUNTRY": country_code,
                "OTP_TIMEOUT": str(int(timeout)),
                "OAUTH_CODEX_RT_EXCHANGE": "1",
                "OAUTH_CODEX_RT_BEFORE_CALLBACK": "1",
            }
            login_flow = AuthFlow(
                cfg,
                env_overrides=env_overrides,
                account_callback=lambda em: {
                    "password": account_info.get("password") or "",
                    "totp_secret": account_info.get("totp_secret") or "",
                },
            )
            login_res = login_flow.run_protocol_login(
                mail_provider=mail_provider,
                email=email,
                password=account_info.get("password") or "",
            )
            if not login_res or not (login_res.access_token or login_res.refresh_token):
                raise RuntimeError("AuthFlow 兜底重登未获取到有效凭证")

            new_at = login_res.access_token or ""
            new_rt = login_res.refresh_token or ""
            new_it = login_res.id_token or ""
            claims = _get_account_claims(new_at)
            cpa_doc = {
                "access_token": new_at,
                "refresh_token": new_rt,
                "id_token": new_it,
                "email": email,
                "name": claims.get("name") or "",
                "user_id": claims.get("user_id") or "",
                "account_id": claims.get("account_id") or "",
                "plan_type": claims.get("plan_type") or "free",
                "expires_at": claims.get("exp_iso"),
                "token_type": "Bearer",
                "last_refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "refresh_method": "full_oauth",
            }
            sub2_doc = cpa_credential_to_sub2_account(cpa_doc)
            db.update_registered_oauth(
                email=email,
                access_token=new_at,
                refresh_token=new_rt,
                id_token=new_it,
                cookie_header=login_res.cookie_header or "",
                extra_data={
                    "oauth_export": {
                        "status": "success",
                        "updated_at": time.time(),
                        "claims": claims,
                    }
                },
            )
            _log(f"🎉 [AuthFlow 兜底重登成功] access_token(len={len(new_at)}), refresh_token(len={len(new_rt)}) 已自动写入数据库")
            path_steps.append("authflow_fallback")
            _trace_put(
                trace,
                login_path="+".join(path_steps) if path_steps else "authflow_fallback",
                plan_type=claims.get("plan_type") or "free",
            )
            return {
                "status": "success",
                "label": "✅ 登录成功",
                "access_token": new_at,
                "refresh_token": new_rt,
                "id_token": new_it,
                "account_id": claims.get("account_id") or "",
                "plan_type": claims.get("plan_type") or "free",
                "exp_iso": claims.get("exp_iso"),
                "cpa": cpa_doc,
                "sub2api": sub2_doc,
                "trace": dict(trace),
            }

        raise RuntimeError(f"提交邮箱失败: HTTP {step_resp.status_code} - {err_msg}")

    step_data = _json_or_empty(step_resp)
    page_type, continue_url, verify_mode = _parse_auth_page(step_data)
    _log(f"[2/6] 邮箱已提交: HTTP {step_resp.status_code} {_describe_auth_page(page_type, continue_url, verify_mode)}")
    _trace_put(trace, first_page_type=page_type or _page_kind_from_url(continue_url))
    if page_type:
        path_steps.append(page_type)

    password = str((account_info or {}).get("password") or "").strip()
    totp_secret = str((account_info or {}).get("totp_secret") or "").strip()
    _log(f"[2/6] 本地凭证: 登录密码={'有' if password else '无'}  2FA={'有' if totp_secret else '无'}")
    try:
        has_sess = bool(session.cookies.get("oai-client-auth-session"))
    except Exception:
        has_sess = False
    _log(f"[2/6] 会话 cookie oai-client-auth-session={'有' if has_sess else '无'}")

    login_passed = False
    otp_kicked = False
    password_accepted = False

    if _is_password_page(page_type, continue_url):
        if password:
            _log("[2/6] 当前是密码页，库里有登录密码，提交 password/verify")
            try:
                session.get(
                    f"https://auth.openai.com/log-in/password?email={quote(email)}",
                    headers=nav_headers,
                    timeout=timeout,
                )
            except Exception as e:
                _log(f"[2/6] 打开密码页 GET 失败（继续提交验证）: {e}")
            pw_headers = dict(post_headers)
            pw_headers["Referer"] = "https://auth.openai.com/log-in/password"
            pw_resp = session.post(
                "https://auth.openai.com/api/accounts/password/verify",
                headers=pw_headers,
                json={"password": password},
                allow_redirects=False,
                timeout=timeout,
            )
            step_data = _json_or_empty(pw_resp)
            page_type, continue_url, verify_mode = _parse_auth_page(step_data)
            _log(f"[2/6] 密码验证 HTTP {pw_resp.status_code} {_describe_auth_page(page_type, continue_url, verify_mode)}")
            if pw_resp.status_code != 200:
                raise RuntimeError(
                    f"密码验证失败: HTTP {pw_resp.status_code} - {(pw_resp.text or '')[:180]}"
                )
            if _is_password_page(page_type, continue_url):
                _log("[2/6] 提交密码后仍停在密码页，未视为已登录")
            else:
                password_accepted = True
                _log("[2/6] 密码已受理，离开密码页")
        else:
            _log("[2/6] 当前是密码页，但库里没有登录密码，不会提交空密码，也不会假装已经登录")
            _log("[3/6] 改为向 OpenAI 申请邮箱一次性验证码")
            sent_ok, sent_pt, sent_cu, sent_md = _kickoff_login_email_otp(session, post_headers, timeout, _log)
            if not sent_ok:
                raise RuntimeError(
                    "停在密码页且库里没有登录密码，向 OpenAI 申请邮箱验证码也失败，无法继续授权"
                )
            otp_kicked = True
            path_steps.append("otp_from_password")
            _trace_put(trace, need_otp=True)
            otp_issued_after = time.time() - 5
            if sent_pt:
                page_type = sent_pt
            else:
                page_type = "email_otp_verification"
            if sent_cu:
                continue_url = sent_cu
            elif "/email-verification" not in (continue_url or ""):
                continue_url = "https://auth.openai.com/email-verification"
            if sent_md:
                verify_mode = sent_md
            _log(f"[3/6] 发码后 {_describe_auth_page(page_type, continue_url, verify_mode)}")
            if _is_password_page(page_type, continue_url) and not _is_otp_page(page_type, continue_url, verify_mode):
                page_type = "email_otp_verification"
                continue_url = "https://auth.openai.com/email-verification"
                _log("[3/6] 发码接口没返回验证页，按邮箱 OTP 页继续收信")

    page_type, continue_url, verify_mode = _submit_totp_if_needed(
        session, post_headers, page_type, continue_url, totp_secret, timeout, _log, verify_mode
    )
    login_passed = _looks_logged_in(page_type, continue_url, verify_mode)
    need_otp = _is_otp_page(page_type, continue_url, verify_mode) or otp_kicked
    if (
        not need_otp
        and not login_passed
        and not _is_mfa_page(page_type, continue_url)
        and (password_accepted or (not page_type and not continue_url))
    ):
        need_otp = True
        _log("[2/6] 还没有离开登录态的下一页，按邮箱 OTP 继续，不会假装已登录")
    _log(
        f"[2/6] 登录判定: login_passed={login_passed} need_otp={need_otp} "
        f"{_describe_auth_page(page_type, continue_url, verify_mode)}"
    )

    if need_otp:
        if not mail_provider:
            email_lower = (email or "").strip().lower()
            if any(dom in email_lower for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
                raise RuntimeError(
                    f"账号 {email} 需要收取邮箱 OTP 验证码，但号池中未找到该微软邮箱的取件凭证 (email----password----client_id----refresh_token)"
                )
            raise RuntimeError(f"账号 {email} 需要收取邮箱验证码，但未配置可用邮箱服务或未获取到取件凭证")

        path_steps.append("otp")
        _trace_put(trace, need_otp=True)
        _step("3", "[3/6] 取邮箱OTP (收信中...)")
        mail_name = getattr(mail_provider, "display_name", "") or type(mail_provider).__name__
        if otp_kicked:
            _log(f"[3/6] 刚向 OpenAI 申请了邮箱验证码，正在 {mail_name} 收信 (timeout=60s)")
        else:
            _log(
                f"[3/6] 当前是邮箱验证页，按已有账号处理：服务端通常在提交邮箱时已发码，"
                f"正在 {mail_name} 收信 (timeout=60s, issued_after={int(otp_issued_after)})"
            )
        t_otp0 = time.time()
        otp_code = mail_provider.wait_for_otp(email, timeout=60, issued_after=otp_issued_after)
        t_otp = round(time.time() - t_otp0, 1)
        if not otp_code:
            raise RuntimeError(f"收取邮箱 OTP 验证码超时 ({t_otp}s) 或未收到邮件")
        _log(f"[3/6] 收到邮箱 OTP: {otp_code} (耗时 {t_otp}s)")

        _step("4", "[4/6] 校验OTP (验证码核验)")
        _log(f"[4/6] 正在提交邮箱 OTP: {otp_code}")
        st_token_v, so_token_v = "", ""
        try:
            st_token_v, so_token_v = get_sentinel_token(
                session,
                device_id=device_id,
                flow="authorize_continue",
                user_agent=ua,
                lang_full=lang_full,
            )
        except Exception as e:
            _log(f"[4/6] OTP 提交前 Sentinel 计算失败（继续用原 token）: {e}")

        otp_headers = dict(post_headers)
        otp_headers["Referer"] = "https://auth.openai.com/email-verification"
        if st_token_v:
            otp_headers["openai-sentinel-token"] = st_token_v
        if so_token_v:
            otp_headers["openai-sentinel-so-token"] = so_token_v

        v_resp = session.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers=otp_headers,
            json={"code": otp_code},
            allow_redirects=False,
            timeout=timeout,
        )
        step_data = _json_or_empty(v_resp)
        page_type, continue_url, verify_mode = _parse_auth_page(step_data)
        _log(f"[4/6] 邮箱 OTP 校验 HTTP {v_resp.status_code} {_describe_auth_page(page_type, continue_url, verify_mode)}")
        if v_resp.status_code != 200:
            raise RuntimeError(f"邮箱 OTP 验证失败: HTTP {v_resp.status_code} - {(v_resp.text or '')[:150]}")
        if _is_otp_page(page_type, continue_url, verify_mode):
            raise RuntimeError("邮箱 OTP 已提交，但仍停在邮箱验证页，验证码可能无效")
        if _still_needs_login(page_type, continue_url):
            raise RuntimeError(
                f"邮箱 OTP 已提交，但又回到登录/密码页：{_describe_auth_page(page_type, continue_url, verify_mode)}"
            )
        login_passed = True
        _log("[4/6] 邮箱 OTP 校验通过，离开验证页")
    elif login_passed:
        _log(f"[3/6] 本步不需要邮箱 OTP。当前 {_describe_auth_page(page_type, continue_url, verify_mode)}")
    else:
        raise RuntimeError(
            f"登录未完成，当前 {_describe_auth_page(page_type, continue_url, verify_mode)}。"
            "不会假装已登录去跟跳转拿 callback code"
        )

    page_type, continue_url, verify_mode = _submit_totp_if_needed(
        session, post_headers, page_type, continue_url, totp_secret, timeout, _log, verify_mode
    )
    if _still_needs_login(page_type, continue_url) or _is_otp_page(page_type, continue_url, verify_mode):
        raise RuntimeError(
            f"登录未完成，当前 {_describe_auth_page(page_type, continue_url, verify_mode)}。"
            "不会把登录/验证页当成工作空间去跟跳转"
        )

    # 检测是否命中手机号验证
    if page_type == "add_phone" or "/add-phone" in continue_url or "/phone-verification" in continue_url:
        sms_cfg = account_info.get("sms_config") or {}
        sms_enabled = bool(sms_cfg.get("sms_enabled", False))

        if not sms_enabled or skip_sms:
            _log("检测到需要手机号验证 (未开启 SMS 接码，已按要求跳过接码并标记)")
            path_steps.append("add_phone")
            _trace_put(trace, need_phone=True, login_path="+".join(path_steps), continue_page_type="add_phone")
            return {
                "status": "need_phone",
                "label": "需接码(已跳过)",
                "error": "OpenAI 要求绑定手机号 (已跳过)",
                "trace": dict(trace),
            }

        add_phone_url = continue_url or "https://auth.openai.com/add-phone"
        _log("[5/6] 登录后要绑手机。先打开绑手机页，把这次登录会话挂上，再去租号")
        opened = _enter_auth_page(session, add_phone_url, nav_headers, timeout, _log, "[5/6] 绑手机页")
        if _page_kind_from_url(opened) in ("登录页", "密码页"):
            raise RuntimeError(
                "2FA 后打开绑手机页又回到登录，这次登录会话没有保住。"
                "账号测活正常不代表这次 OAuth 会话还在，不会去租号浪费钱"
            )

        # ── 开启接码：执行 SmsBower 自动租号、发码、收码与验证 ──
        from sms_provider import PhoneCallbackController, parse_price_spec

        provider_key = str(sms_cfg.get("sms_provider") or "smsbower").strip().lower()
        api_key = str(sms_cfg.get("sms_api_key") or "").strip()
        country = str(sms_cfg.get("sms_country") or "52").strip()
        max_price_raw = sms_cfg.get("sms_max_price") or sms_cfg.get("sms_price")
        min_p, max_p, exact_p = parse_price_spec(max_price_raw)
        max_attempts = max(1, min(10, int(sms_cfg.get("sms_max_attempts") or 3)))
        raw_timeout = int(sms_cfg.get("sms_timeout") or 75)
        # OpenAI 的 /add-phone 授权会话总生命周期仅约 150~180 秒，单个号码超时若超过 90 秒会导致超时后整个会话过期报 400 invalid_auth_step
        if raw_timeout > 90:
            _log(f"[sms] 💡 提示: 原配置超时 {raw_timeout}s 过长易致 OpenAI 会话失效，已自动优化为 85s 安全周期")
            per_phone_timeout = 85
        else:
            per_phone_timeout = max(20, raw_timeout)

        # 候选国家列表
        if country == "AUTO":
            allowed_countries = "52,6,10,73,15,16,12"
            auto_select_country = True
            primary_country = "52"
        else:
            # 用户指定了固定国家：严格只使用固定国家，绝不轮询其它任何国家
            allowed_countries = country
            auto_select_country = False
            primary_country = country

        price_desc = "不限"
        if exact_p > 0:
            price_desc = f"锁定指定金额 {exact_p}"
        elif min_p > 0 and max_p > 0:
            price_desc = f"金额区间 {min_p}~{max_p}"
        elif min_p > 0:
            price_desc = f"最低金额 {min_p}"
        elif max_p > 0:
            price_desc = f"最高限价 {max_p}"

        provider_ids = str(sms_cfg.get("sms_provider_ids") or sms_cfg.get("providerIds") or sms_cfg.get("sms_operator") or "").strip()
        except_provider_ids = str(sms_cfg.get("sms_except_provider_ids") or sms_cfg.get("exceptProviderIds") or "").strip()
        path_steps.append("add_phone")
        _trace_put(
            trace,
            need_phone=True,
            sms_enabled=True,
            sms_provider=provider_key,
            sms_country=country,
            sms_provider_ids=provider_ids,
            sms_except_provider_ids=except_provider_ids,
            sms_price_spec=str(max_price_raw or ""),
        )

        _step("5_sms", f"[5/6] 手机号接码 ({provider_key})")
        id_tip = f", 指定供应商={provider_ids}" if provider_ids else ""
        _log(f"[5/6] 遇到手机验证，已启用 {provider_key} 接码 (国家={country}{id_tip}, 金额要求={price_desc}, 最多换号={max_attempts}次, 超时={per_phone_timeout}s)...")

        ctrl = PhoneCallbackController(
            provider_key=provider_key,
            config={
                "sms_api_key": api_key,
                "sms_country": primary_country,
                "sms_allowed_countries": allowed_countries,
                "sms_service": "dr",
                "sms_price": max_price_raw,
                "sms_max_price": max_p,
                "sms_min_price": min_p,
                "sms_exact_price": exact_p,
                "sms_provider_ids": provider_ids,
                "sms_except_provider_ids": except_provider_ids,
                "sms_per_phone_timeout": str(per_phone_timeout),
                "sms_max_phone_attempts": str(max_attempts),
            },
            service="dr",
            country=primary_country,
            auto_select_country=auto_select_country,
            log_fn=lambda m: _log(f"[sms] {m}"),
        )

        phone_verified = False
        verified_phone = ""
        last_sms_err = ""
        try:
            for attempt in range(1, max_attempts + 1):
                remain_cnt = max_attempts - attempt
                _step("5_sms_rent", f"[5/6] 租号中 ({attempt}/{max_attempts} 剩{remain_cnt})")
                _log(f"[sms] 🔁 正在租用手机号 (第 {attempt}/{max_attempts} 个，剩余备选 {remain_cnt} 次)...")
                phone = ""
                try:
                    phone = ctrl.get_phone()
                except Exception as e:
                    last_sms_err = str(e)
                    _log(f"[sms] 租号失败: {e}")
                    time.sleep(2)
                    continue
                if not phone:
                    last_sms_err = "接码平台未返回有效手机号"
                    time.sleep(2)
                    continue

                _step("5_sms_send", f"[5/6] 发送短信: {phone} (第{attempt}/{max_attempts}号)")
                _log(f"[sms] 租到手机号: {phone}，正在向 OpenAI 提交发送验证短信...")
                phone_headers = dict(post_headers)
                phone_headers["Referer"] = "https://auth.openai.com/add-phone"
                phone_headers["Origin"] = "https://auth.openai.com"
                if device_id:
                    phone_headers["oai-device-id"] = device_id

                def _send_phone():
                    return session.post(
                        "https://auth.openai.com/api/accounts/add-phone/send",
                        headers=phone_headers,
                        json={"phone_number": phone},
                        timeout=30,
                    )

                send_resp = _send_phone()
                err_msg = (send_resp.text or "")[:180]
                err_lc = err_msg.lower()
                session_dead = (
                    send_resp.status_code in (401, 403, 409)
                    or "no longer valid" in err_lc
                    or "invalid_state" in err_lc
                    or "invalid_auth_step" in err_lc
                )
                if session_dead:
                    _log(f"[sms] add-phone/send HTTP {send_resp.status_code} 会话失效/步骤过期: {err_msg}")
                    _log("[sms] 不立刻判账号死。重新打开绑手机页再发一次")
                    opened = _enter_auth_page(
                        session,
                        opened or add_phone_url,
                        nav_headers,
                        timeout,
                        _log,
                        "[sms] 绑手机页重开",
                    )
                    if _page_kind_from_url(opened) in ("登录页", "密码页"):
                        ctrl.mark_send_failed("session_expired")
                        raise RuntimeError(
                            "绑手机时登录会话已超时失效（打开 add-phone 回到登录页）。"
                            "通常是因为单个号码等待短信时间过长（超过 OpenAI 会话 TTL），建议将接码超时设为 60~80 秒"
                        )
                    send_resp = _send_phone()
                    err_msg = (send_resp.text or "")[:180]
                    err_lc = err_msg.lower()
                    session_dead = (
                        send_resp.status_code in (401, 403, 409)
                        or "no longer valid" in err_lc
                        or "invalid_state" in err_lc
                        or "invalid_auth_step" in err_lc
                    )

                if send_resp.status_code != 200:
                    if session_dead:
                        _log(f"[sms] 会话已失效 HTTP {send_resp.status_code}: {err_msg}，立即停止租号并释放退款")
                        ctrl.mark_send_failed("session_expired")
                        raise RuntimeError(
                            f"OpenAI 绑手机接口会话已超时失效 (HTTP {send_resp.status_code})。"
                            "通常是因为上一号码等待时间过长导致 OpenAI 授权会话过期，已自动释放号码退款。"
                        )
                    _log(f"[sms] OpenAI 拒绝该手机号 HTTP {send_resp.status_code}: {err_msg}，退号换下一个")
                    ctrl.mark_send_failed(err_msg)
                    time.sleep(2)
                    continue

                ctrl.mark_send_succeeded()
                _step("5_sms_wait", f"[5/6] 等收短信: {phone} (第{attempt}/{max_attempts}号 剩{remain_cnt})")
                _log(f"[sms] 📩 短信已成功发送至 {phone}，正在轮询等待验证码 (timeout={per_phone_timeout}s)...")

                sms_code = ""
                try:
                    sms_code = ctrl.get_code(timeout=per_phone_timeout)
                except Exception as e:
                    _log(f"[sms] ⏱️ 等待短信超时或异常: {e}，立即取消并释放退款该号码...")
                    ctrl.mark_send_failed("timeout_no_sms")  # 释放退款
                    time.sleep(2)
                    continue

                if not sms_code:
                    _log(f"[sms] ⏱️ 未在 {per_phone_timeout}s 内收到短信，立即取消并释放退款该号码...")
                    ctrl.mark_send_failed("timeout_no_sms")  # 释放退款
                    time.sleep(2)
                    continue

                _log(f"[sms] ✅ 成功收取到短信验证码: {sms_code}，正在提交校验...")
                val_headers = dict(phone_headers)
                val_headers["Referer"] = "https://auth.openai.com/phone-verification"
                val_resp = session.post(
                    "https://auth.openai.com/api/accounts/phone-otp/validate",
                    headers=val_headers,
                    json={"code": sms_code},
                    timeout=30,
                )
                if val_resp.status_code != 200:
                    _log(f"[sms] ❌ 手机验证码校验失败 ({val_resp.status_code}): {(val_resp.text or '')[:120]}，取消并退款该号码...")
                    ctrl.mark_send_failed("validate_failed")  # 释放退款
                    time.sleep(2)
                    continue

                _log(f"[sms] 手机号 {phone} 验证 HTTP {val_resp.status_code}，准备进入下一页")
                ctrl.report_success()
                phone_verified = True
                verified_phone = phone
                page_type, continue_url, verify_mode = _parse_auth_page(_json_or_empty(val_resp))
                _log(f"[sms] 验号后 {_describe_auth_page(page_type, continue_url, verify_mode)}")
                act = getattr(ctrl, "activation", None)
                meta = (getattr(act, "metadata", None) or {}) if act else {}
                _trace_put(
                    trace,
                    phone_verified=True,
                    sms_attempts=attempt,
                    sms_country_used=str(getattr(act, "country", "") or country),
                    sms_phone_prefix=_phone_prefix(phone),
                    sms_cost=meta.get("cost"),
                    sms_operator=str(meta.get("operator") or ""),
                )
                break
        finally:
            ctrl.cleanup()
            ctrl._release_lock()

        if not phone_verified:
            raise RuntimeError(f"接码失败 (已尝试 {max_attempts} 个号码均已安全退回): {last_sms_err or '未收到短信'}")

    # ──────────────── 阶段 5: 选择工作区与捕获回调 ────────────────
    _step("5", "[5/6] 选工作区 (提取回调)")

    # 提取已注册账号的 chatgpt_account_id 作为 workspace 兜底
    fallback_wid = ""
    if account_info:
        fallback_wid = str(account_info.get("account_id") or account_info.get("chatgpt_account_id") or "").strip()
        if not fallback_wid and account_info.get("access_token"):
            try:
                payload = _decode_jwt_payload(account_info["access_token"])
                fallback_wid = str(payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id") or "").strip()
            except Exception:
                pass

    # 规范化启动 candidate URLs（清除强制 prompt=login 阻断参数）
    clean_continue = continue_url or ""
    if clean_continue:
        for p in ("&prompt=login", "prompt=login&", "prompt=login", "&prompt=select_account", "prompt=select_account&", "prompt=select_account"):
            clean_continue = clean_continue.replace(p, "")

    clean_auth = auth_url
    for p in ("&prompt=login", "prompt=login&", "prompt=login", "&prompt=select_account", "prompt=select_account&", "prompt=select_account"):
        clean_auth = clean_auth.replace(p, "")

    raw_candidates = [
        clean_continue,
        clean_auth,
        continue_url,
        auth_url,
    ]
    start_candidates = []
    for u in raw_candidates:
        u_str = (u or "").strip()
        if not u_str or u_str in start_candidates:
            continue
        kind = _page_kind_from_url(u_str)
        if kind in ("密码页", "登录页"):
            _log(f"[5/6] 候选起点丢掉（{kind}，未完成登录）: {_short_url(u_str)}")
            continue
        start_candidates.append(u_str)
        _log(f"[5/6] 候选起点 {len(start_candidates)}: {kind} {_short_url(u_str)}")

    if not start_candidates:
        raise RuntimeError(
            f"登录后没有可跟的跳转起点。当前 {_describe_auth_page(page_type, continue_url, verify_mode)}。"
            "不会拿登录/密码页去假装跟 OAuth callback"
        )

    callback_url = _follow_oauth_callback(
        session=session,
        start_urls=start_candidates,
        redirect_uri=redirect_uri,
        nav_headers=nav_headers,
        post_headers=post_headers,
        device_id=device_id,
        timeout=timeout,
        log_fn=_log,
        fallback_workspace_id=fallback_wid,
    )

    if not callback_url or "code=" not in callback_url:
        raise RuntimeError(
            "未能拿到 callback authorization code。"
            f"跟跳转前 {_describe_auth_page(page_type, continue_url, verify_mode)}。"
            "若日志里停在登录/密码页，说明这一步其实没登录成功，不是工作空间丢了 code"
        )

    qs = parse_qs(urlparse(callback_url).query)
    code = (qs.get("code") or [""])[0].strip()
    returned_state = (qs.get("state") or [""])[0].strip()
    if returned_state and returned_state != state:
        raise RuntimeError("OAuth state 不匹配，可能存在 CSRF 风险")
    if not code:
        raise RuntimeError("callback URL 中未找到 authorization code")

    _log(f"[5/6] 成功捕获 OAuth Callback Code: {code[:16]}...")

    # ──────────────── 阶段 6: 换取 Token ────────────────
    _step("6", "[6/6] 换取Token (OAuth交换)")
    _log("[6/6] 正在使用 authorization code 换取 Refresh Token ...")
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": ua,
        "Origin": "https://auth.openai.com",
    }
    token_form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    t_resp = None
    for token_try in range(3):
        try:
            t_resp = session.post(
                "https://auth.openai.com/oauth/token",
                headers=token_headers,
                data=urlencode(token_form),
                timeout=timeout,
            )
            break
        except Exception as t_err:
            if token_try < 2:
                _log(f"[6/6] 换取 Token 网络重试 ({token_try+1}/2): {t_err}")
                time.sleep(1.5)
            else:
                raise RuntimeError(f"换取 token 网络连接异常: {t_err}")

    if t_resp is None or t_resp.status_code != 200:
        err_body = (getattr(t_resp, "text", "") or "")[:180] if t_resp is not None else "无响应"
        status_code = getattr(t_resp, "status_code", 0)
        raise RuntimeError(f"换取 token 失败: HTTP {status_code} - {err_body}")

    t_data = t_resp.json()
    at = t_data.get("access_token") or ""
    rt = t_data.get("refresh_token") or ""
    it = t_data.get("id_token") or ""

    if not rt:
        raise RuntimeError("OpenAI 返回的 Token 响应中缺少 refresh_token")

    claims = _get_account_claims(at) if at else {}
    plan_type = claims.get("plan_type") or "free"
    account_id = claims.get("account_id") or ""
    exp_iso = claims.get("exp_iso") or ""

    _log(f"[6/6] ✅ 成功获取 Refresh Token (长度={len(rt)}), 计划类型={plan_type}, 账号ID={account_id[:8]}...")
    _trace_put(
        trace,
        continue_page_type=page_type,
        continue_kind=_page_kind_from_url(continue_url),
        plan_type=plan_type,
        login_path="+".join(path_steps) if path_steps else "direct",
    )
    return {
        "status": "success",
        "label": f"成功 ({plan_type.upper()})",
        "access_token": at,
        "refresh_token": rt,
        "id_token": it,
        "account_id": account_id,
        "plan_type": plan_type,
        "exp_iso": exp_iso,
        "phone_verified": phone_verified,
        "verified_phone": verified_phone,
        "trace": dict(trace) if isinstance(trace, dict) else {},
    }


def _run_one_oauth_export(task: OAuthExportTask, email: str) -> None:
    if task.cancelled:
        try:
            db.insert_oauth_attempt_feature({
                "task_id": task.task_id,
                "email": (email or "").strip().lower(),
                "outcome": "cancelled",
                "error_class": "cancelled",
                "error_text": "任务被中止",
            })
        except Exception:
            pass
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被中止"})
        return

    task.set_running(email, step_text="[1/6] 建立会话")
    task.add_email_log(email, f"开始重跑 OAuth 导出: {email}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "not_found", "label": "未找到", "error": "数据库中无此凭证记录"}
        task.add_email_log(email, "错误: 数据库中无此凭证记录")
        try:
            db.insert_oauth_attempt_feature({
                "task_id": task.task_id,
                "email": (email or "").strip().lower(),
                "outcome": "not_found",
                "error_class": "not_found",
                "error_text": "数据库中无此凭证记录",
            })
        except Exception:
            pass
        task.mark_done(email, res)
        return

    # 1. 代理路由
    proxy = task.next_proxy()
    raw_country = (task.config.get("proxy_country") or cred.get("reg_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country)
    if proxy and target_country:
        proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

    proxy_label = proxy.split("@")[-1] if "@" in proxy else (proxy or "直连")
    country_tip = f" (目标国家: {target_country})" if target_country else ""
    task.add_email_log(email, f"使用网络出口: {proxy_label}{country_tip}")

    # 2. 邮箱取码准备
    email_lower = (email or "").strip().lower()
    mail_account = db.get_account(email_lower)
    saved_oauth = {}
    if cred.get("extra"):
        saved_oauth = cred["extra"].get("mail_oauth") or {}
    if not isinstance(saved_oauth, dict):
        saved_oauth = {}

    mail_source = ""
    # 优先使用注册时绑定的 mail_oauth 凭证（涵盖 Remail / 微软 OAuth / iCloud 中转）
    if saved_oauth.get("kind") == "remail" or saved_oauth.get("service_token") or saved_oauth.get("pickup_url"):
        mail_source = "remail"
        mail_account = {
            "email": email_lower,
            "service_token": saved_oauth.get("service_token", ""),
            "pickup_url": saved_oauth.get("pickup_url", ""),
            "order_no": saved_oauth.get("order_no", ""),
            "project_id": saved_oauth.get("project_id", 2),
            "email_suffix": saved_oauth.get("email_suffix", "icloud.com"),
            "service_mode": saved_oauth.get("service_mode", "purchase"),
            "kind": "remail",
        }
    elif saved_oauth.get("kind") == "icloud_relay" or saved_oauth.get("relay_url"):
        mail_source = "icloud_relay"
        mail_account = {
            "email": email_lower,
            "relay_url": saved_oauth.get("relay_url", ""),
            "kind": "icloud_relay",
        }
    elif not mail_account and (saved_oauth.get("refresh_token") or saved_oauth.get("password")):
        mail_account = {
            "email": email_lower,
            "password": saved_oauth.get("password", ""),
            "client_id": saved_oauth.get("client_id", ""),
            "refresh_token": saved_oauth.get("refresh_token", ""),
            "kind": saved_oauth.get("kind", "outlook"),
        }

    # 兜底：如果 registered 没记全，但该邮箱在 remail_recycle_pool 中有记录
    if not mail_source:
        try:
            cur_pool = db._conn().execute(
                "SELECT service_token, order_no, project_id, email_suffix, service_mode FROM remail_recycle_pool WHERE email=?",
                (email_lower,),
            ).fetchone()
            if cur_pool and cur_pool["service_token"]:
                mail_source = "remail"
                mail_account = {
                    "email": email_lower,
                    "service_token": cur_pool["service_token"],
                    "order_no": cur_pool.get("order_no", ""),
                    "project_id": cur_pool.get("project_id", 2),
                    "email_suffix": cur_pool.get("email_suffix", "icloud.com"),
                    "service_mode": cur_pool.get("service_mode", "purchase"),
                    "kind": "remail",
                }
        except Exception:
            pass

    # 若仍未确定，根据号池或域名后缀推断
    if not mail_source:
        if mail_account and mail_account.get("kind"):
            mail_source = str(mail_account.get("kind")).strip().lower()
        elif any(dom in email_lower for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            mail_source = "outlook"
        elif any(dom in email_lower for dom in ("@icloud.", "@me.", "@mac.")):
            def_source = (db.get_setting("mail_source", "") or "").strip().lower()
            mail_source = "remail" if def_source == "remail" else "icloud_relay"
        elif cred.get("kind"):
            mail_source = str(cred.get("kind")).strip().lower()
        else:
            mail_source = (db.get_setting("mail_source", "") or "cf_temp").strip().lower()

    mail = None
    account_for_mail = {**cred, **(mail_account or {"email": email_lower})}
    try:
        mail = create_mail_provider(mail_source, db.get_mail_settings(), account_for_mail)
    except Exception as e:
        task.add_email_log(email, f"邮箱 Provider ({mail_source}) 初始化提示: {e}")
        mail = None

    sms_cfg = task.config.get("sms_config") or {}
    sms_enabled = bool(sms_cfg.get("sms_enabled", False))
    cred_with_sms = dict(cred)
    cred_with_sms["sms_config"] = sms_cfg

    created_at = cred.get("created_at")
    account_age_days = None
    try:
        if created_at:
            account_age_days = round(max(0.0, (time.time() - float(created_at)) / 86400.0), 2)
    except Exception:
        account_age_days = None

    flow_trace: dict[str, Any] = {
        "task_id": task.task_id,
        "email": email_lower,
        "email_domain": email_lower.split("@")[-1] if "@" in email_lower else "",
        "mail_kind": mail_source,
        "has_mail_cred": bool(mail_account),
        "account_age_days": account_age_days,
        "sms_enabled": sms_enabled,
        "sms_provider": str(sms_cfg.get("sms_provider") or ""),
        "sms_country": str(sms_cfg.get("sms_country") or ""),
        "sms_provider_ids": str(sms_cfg.get("sms_provider_ids") or ""),
        "sms_except_provider_ids": str(sms_cfg.get("sms_except_provider_ids") or ""),
        "sms_price_spec": str(sms_cfg.get("sms_max_price") or sms_cfg.get("sms_price") or ""),
        "proxy_country": target_country,
        "proxy_host": _proxy_host_of(proxy),
        "has_password": bool(str(cred.get("password") or "").strip()),
        "has_totp": bool(str(cred.get("totp_secret") or "").strip()),
    }

    def _persist_oauth_feat(outcome: str, error_text: str = "", duration_ms: int = 0, extra: Optional[dict] = None):
        feat = dict(flow_trace)
        if extra:
            feat.update(extra)
        feat["outcome"] = outcome
        feat["error_text"] = (error_text or "")[:400]
        feat["error_class"] = _classify_oauth_error(error_text, outcome)
        feat["duration_ms"] = int(duration_ms or 0)
        try:
            db.insert_oauth_attempt_feature(feat)
        except Exception as persist_err:
            logger.warning("[oauth_export] 特征落库失败: %s", persist_err)

    started_ts = time.time()
    try:
        flow_res = execute_codex_oauth_flow(
            email=email,
            mail_provider=mail,
            proxy=proxy,
            target_country=target_country,
            account_info=cred_with_sms,
            log_fn=lambda msg: task.add_email_log(email, msg),
            step_fn=lambda step_k, step_t: task.set_step(email, step_k, step_t),
            skip_sms=not sms_enabled,
            timeout=float(task.config.get("timeout") or 45.0),
            trace=flow_trace,
        )
        req_ms = int((time.time() - started_ts) * 1000)

        # 检查是否需接码
        if flow_res.get("status") == "need_phone":
            res = {
                "status": "need_phone",
                "label": "需接码(已跳过)",
                "error": "OpenAI 要求绑定手机号 (已跳过)",
                "req_ms": req_ms,
            }
            db.update_registered_oauth_status(email, "need_phone", "需要手机号验证 (已跳过)")
            _persist_oauth_feat("need_phone", "OpenAI 要求绑定手机号 (已跳过)", req_ms, flow_res.get("trace"))
            task.mark_done(email, res)
            return
        if flow_res.get("status") == "cancelled":
            _persist_oauth_feat("cancelled", "任务被中止", req_ms, flow_res.get("trace"))
            task.mark_done(email, flow_res)
            return
        if flow_res.get("status") not in ("success", None, "") and not flow_res.get("access_token") and not flow_res.get("refresh_token"):
            st = str(flow_res.get("status") or "error")
            err = str(flow_res.get("error") or flow_res.get("label") or st)
            _persist_oauth_feat(st, err, req_ms, flow_res.get("trace"))
            task.mark_done(email, {**flow_res, "req_ms": req_ms})
            return

        at = flow_res.get("access_token") or cred.get("access_token") or ""
        rt = flow_res.get("refresh_token") or ""
        it = flow_res.get("id_token") or cred.get("id_token") or ""
        account_id = flow_res.get("account_id") or ""
        plan_type = flow_res.get("plan_type") or "free"
        exp_iso = flow_res.get("exp_iso") or ""

        # 构造 CPA 格式 JSON
        cpa_data = {
            "type": "codex",
            "email": email,
            "access_token": at,
            "refresh_token": rt,
            "id_token": it,
            "account_id": account_id,
            "plan_type": plan_type,
            "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expired": exp_iso,
        }

        # 构造 Sub2API 格式 JSON
        sub2_account = cpa_credential_to_sub2_account(cpa_data)

        # 落盘本地文件
        cpa_file = CPA_DIR / f"codex-{email}.json"
        sub2_file = SUB2_DIR / f"sub2-{email}.json"
        cpa_file.write_text(json.dumps(cpa_data, ensure_ascii=False, indent=2), encoding="utf-8")
        sub2_file.write_text(json.dumps(sub2_account, ensure_ascii=False, indent=2), encoding="utf-8")

        phone_verified = bool(flow_res.get("phone_verified") or (flow_res.get("trace") or {}).get("phone_verified"))
        verified_phone = str(flow_res.get("verified_phone") or (flow_res.get("trace") or {}).get("sms_phone_prefix") or "")
        auth_method = "phone_verified" if phone_verified else "no_phone_needed"
        oauth_status = "success_phone" if phone_verified else "success_direct"

        # 回写数据库 registered 表
        db.update_registered_oauth(
            email=email,
            access_token=at,
            refresh_token=rt,
            id_token=it,
            cookie_header=cred.get("cookie_header") or "",
            oauth_status=oauth_status,
            extra_data={
                "oauth_export": {
                    "status": oauth_status,
                    "auth_method": auth_method,
                    "phone_verified": phone_verified,
                    "verified_phone": verified_phone,
                    "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "plan_type": plan_type,
                    "account_id": account_id,
                }
            },
        )

        res = {
            "status": "success",
            "label": f"成功 ({plan_type.upper()})",
            "access_token_len": len(at),
            "refresh_token_len": len(rt),
            "id_token_len": len(it),
            "plan_type": plan_type,
            "account_id": account_id,
            "cpa": cpa_data,
            "sub2api": sub2_account,
            "req_ms": req_ms,
        }
        extra_trace = flow_res.get("trace") if isinstance(flow_res.get("trace"), dict) else {}
        extra_trace["plan_type"] = plan_type
        _persist_oauth_feat("success", "", req_ms, extra_trace)
        task.add_email_log(email, f"✅ OAuth 导出成功 ({req_ms}ms): RT={len(rt)} 字符, Plan={plan_type}, AccountId={account_id[:8]}...")
        task.mark_done(email, res)

    except Exception as e:
        req_ms = int((time.time() - started_ts) * 1000)
        err_str = str(e)
        if ("add_phone" in err_str.lower() or "手机" in err_str) and not sms_enabled:
            res = {"status": "need_phone", "label": "需接码(已跳过)", "error": "需要手机号验证 (已跳过)", "req_ms": req_ms}
            db.update_registered_oauth_status(email, "need_phone", "需要手机号验证 (已跳过)")
            task.add_email_log(email, "检测到需要手机号验证 (未开启自动接码，已跳过)")
            _persist_oauth_feat("need_phone", "需要手机号验证 (已跳过)", req_ms)
        else:
            is_sms_fail = sms_enabled and ("接码" in err_str or "NO_NUMBERS" in err_str or "短信" in err_str)
            is_business_fail = is_sms_fail or any(k in err_str for k in ("密码", "password", "封禁", "banned", "取件凭证", "未找到", "400", "invalid_grant", "access_denied"))
            fail_status = "failed" if is_business_fail else "error"
            fail_label = "接码失败" if is_sms_fail else ("授权失败" if is_business_fail else "授权异常")
            res = {"status": fail_status, "label": fail_label, "error": err_str, "req_ms": req_ms}
            db.update_registered_oauth_status(email, fail_status, err_str)
            task.add_email_log(email, f"OAuth {fail_label} ({req_ms}ms): {err_str}")
            _persist_oauth_feat(fail_status, err_str, req_ms)
        task.mark_done(email, res)


def _worker_loop(task: OAuthExportTask, email_queue: queue.Queue) -> None:
    while not task.cancelled:
        try:
            email = email_queue.get_nowait()
        except queue.Empty:
            break
        try:
            _run_one_oauth_export(task, email)
        finally:
            email_queue.task_done()


def start(emails: list[str], config: dict) -> str:
    """启动 OAuth 导出任务。"""
    unique_emails = list(dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()))
    if not unique_emails:
        raise ValueError("请提供至少一个要导出的账号邮箱")

    task_id = str(uuid.uuid4())[:12]
    task = OAuthExportTask(task_id, unique_emails, config)

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    workers = max(1, min(20, int(config.get("workers") or 5)))
    email_queue: queue.Queue = queue.Queue()
    for em in unique_emails:
        email_queue.put(em)

    def _run():
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"oauth_export_{task_id}") as pool:
            futures = [pool.submit(_worker_loop, task, email_queue) for _ in range(workers)]
            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    logger.warning(f"[oauth_export] Worker 异常: {e}")

        task.finished_at = time.time()
        try:
            task.queue.put({"kind": "end", "task_id": task_id, "stats": task.stats})
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name=f"OAuthExportTaskRunner-{task_id}")
    t.start()
    return task_id


def stop(task_id: str) -> bool:
    """停止指定的任务。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task:
            task.cancelled = True
            try:
                task.queue.put({"kind": "end", "task_id": task_id, "cancelled": True})
            except Exception:
                pass
            return True
    return False


def snapshot(task_id: str) -> Optional[dict]:
    """获取任务当前的快照。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return None
        with task._lock:
            return {
                "task_id": task.task_id,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "cancelled": task.cancelled,
                "done_count": task.done_count,
                "total": len(task.items),
                "stats": dict(task.stats),
                "items": {k: dict(v) for k, v in task.items.items()},
            }


def get_queue(task_id: str) -> Optional[queue.Queue]:
    with _tasks_lock:
        task = _tasks.get(task_id)
        return task.queue if task else None


def get_logs(task_id: str, email: str) -> list[str]:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            it = task.items.get(email.lower().strip())
            return list(it["logs"]) if it else []


def export_cpa_bundle(task_id: str, emails: Optional[list[str]] = None) -> list[dict]:
    """获取指定任务中所有成功的 CPA 凭证列表。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            cpa_list = []
            target_set = set(e.lower().strip() for e in emails) if emails else None
            for em, it in task.items.items():
                if target_set and em not in target_set:
                    continue
                if it.get("cpa"):
                    cpa_list.append(it["cpa"])
            return cpa_list


def export_sub2_bundle(task_id: str, emails: Optional[list[str]] = None) -> dict:
    """获取指定任务中所有成功账号的 Sub2API 聚合 JSON 数据。"""
    cpa_list = export_cpa_bundle(task_id, emails)
    return build_sub2api_payload(cpa_list)

