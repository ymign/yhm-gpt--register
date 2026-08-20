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
from urllib.parse import parse_qs, quote, urlencode, urlparse

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
    """从 session cookie 或 HTML 中全面提取 workspace_id。"""
    try:
        auth_session = session.cookies.get("oai-client-auth-session", "")
        if not auth_session:
            for c in getattr(session.cookies, "jar", []):
                if getattr(c, "name", "") == "oai-client-auth-session":
                    auth_session = getattr(c, "value", "")
                    break
        if auth_session:
            parts = auth_session.split(".")
            for segment in parts[:3]:
                seg = (segment or "").strip()
                if not seg:
                    continue
                try:
                    padded = seg + "=" * ((4 - len(seg) % 4) % 4)
                    decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace"))
                    if isinstance(decoded, dict):
                        wid = (decoded.get("workspace_id", "") or "").strip()
                        if wid:
                            return wid
                        workspaces = decoded.get("workspaces", [])
                        if isinstance(workspaces, list) and workspaces:
                            for it in workspaces:
                                if isinstance(it, dict):
                                    wid = (it.get("id", "") or "").strip()
                                    if wid:
                                        return wid
                except Exception:
                    pass
    except Exception:
        pass

    if html_text:
        try:
            text = html_text.replace('\\"', '"')
            patterns = [
                r'workspaces".{0,1600}?"id","([0-9a-fA-F-]{36})"',
                r'"workspace_id"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'"workspaceId"\s*:\s*"([0-9a-fA-F-]{36})"',
                r'["\']workspace_id["\']\s*[:=]\s*["\']([0-9a-fA-F-]{36})["\']',
            ]
            for p in patterns:
                m = re.search(p, text, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    return (m.group(1) or "").strip()
        except Exception:
            pass
    return ""


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


def _follow_oauth_callback(
    session,
    start_urls: list[str],
    redirect_uri: str,
    nav_headers: dict,
    post_headers: dict,
    device_id: str,
    timeout: int = 30,
    log_fn: Optional[Callable[[str], None]] = None,
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

        _log(f"[5/6] 正在跟踪授权跳转链路 (尝试 {start_idx+1}/{len(start_urls)})...")

        for hop in range(15):
            if "code=" in curr and ("localhost:1455" in curr or "127.0.0.1" in curr or redirect_uri in curr):
                return curr

            try:
                r = session.get(curr, headers=nav_headers, allow_redirects=False, timeout=timeout)
            except Exception as e:
                _log(f"[5/6] 链路请求异常: {e}")
                break

            status = r.status_code
            loc = (r.headers.get("Location") or r.headers.get("location") or "").strip()
            if loc:
                if loc.startswith("/"):
                    loc = "https://auth.openai.com" + loc
                if "code=" in loc and ("localhost:1455" in loc or "127.0.0.1" in loc or redirect_uri in loc):
                    return loc
                curr = loc
                continue

            # 处理 HTTP 200 页面
            if status == 200:
                html_text = r.text or ""
                # 1. 检查是否是 workspace / consent 页面
                is_workspace_like = (
                    ("/workspace" in curr)
                    or ("/sign-in-with-chatgpt/" in curr)
                    or ("/consent" in curr)
                    or ("workspace" in html_text.lower())
                )
                if is_workspace_like:
                    wid = _extract_workspace_id_from_session(session, html_text)
                    if wid:
                        _log(f"[5/6] 发现授权工作空间 (workspace_id={wid[:8]}...)，正在提交选择...")
                        ws_headers = dict(post_headers)
                        ws_headers["Origin"] = "https://auth.openai.com"
                        ws_headers["Referer"] = curr
                        ws_headers["Content-Type"] = "application/json"
                        if device_id:
                            ws_headers["oai-device-id"] = device_id
                        try:
                            ws_resp = session.post(
                                "https://auth.openai.com/api/accounts/workspace/select",
                                headers=ws_headers,
                                json={"workspace_id": wid},
                                allow_redirects=False,
                                timeout=timeout,
                            )
                            ws_loc = (ws_resp.headers.get("Location") or ws_resp.headers.get("location") or "").strip()
                            if ws_loc:
                                if ws_loc.startswith("/"):
                                    ws_loc = "https://auth.openai.com" + ws_loc
                                if "code=" in ws_loc and ("localhost:1455" in ws_loc or redirect_uri in ws_loc):
                                    return ws_loc
                                curr = ws_loc
                                continue
                            ws_data = ws_resp.json() if ws_resp.status_code == 200 else {}
                            next_url = (ws_data.get("continue_url") or ws_data.get("redirect_url") or "").strip()
                            if next_url:
                                if next_url.startswith("/"):
                                    next_url = "https://auth.openai.com" + next_url
                                curr = next_url
                                continue
                        except Exception as e:
                            _log(f"[5/6] workspace/select 异常: {e}")

                # 2. 检查是否是 /choose-an-account 页面
                if "/choose-an-account" in curr or "choose-an-account" in html_text:
                    next_url = _handle_choose_account_page(session, html_text, curr, post_headers)
                    if next_url:
                        if next_url.startswith("/"):
                            next_url = "https://auth.openai.com" + next_url
                        curr = next_url
                        continue

                # 若是其它 200 页面，尝试搜索 HTML 中是否包含跳转 URL 或 callback code
                m_code = re.search(r"http://localhost:1455/auth/callback\?[^\s\"'<>]+", html_text)
                if m_code:
                    return m_code.group(0)

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
        with self._lock:
            if email in self.items:
                self.items[email]["step"] = step
                self.items[email]["step_text"] = step_text
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step": step,
            "step_text": step_text,
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

    country_code = (target_country or account_info.get("reg_country") or "JP").strip().upper()
    lang_full = COUNTRY_LANG_MAP.get(country_code, "ja-JP,ja;q=0.9,en-US;q=0.8" if country_code == "JP" else "en-US,en;q=0.9")

    # 生成与目标国家对齐的一致性浏览器指纹
    fp = generate_fingerprint(country_code=country_code)
    ua = fp.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    impersonate = fp.get("impersonate") or "chrome136"

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
            return {
                "status": "success",
                "label": "✅ 登录成功",
                "access_token_len": len(new_at),
                "refresh_token_len": len(new_rt),
                "expires_at": claims.get("exp_iso"),
                "plan_type": claims.get("plan_type") or "free",
                "cpa": cpa_doc,
                "sub2api": sub2_doc,
            }

        raise RuntimeError(f"提交邮箱失败: HTTP {step_resp.status_code} - {err_msg}")
        raise RuntimeError(f"提交邮箱失败: HTTP {step_resp.status_code} - {(step_resp.text or '')[:150]}")

    step_data = step_resp.json() if step_resp.status_code == 200 else {}
    page_type = ((step_data.get("page") or {}).get("type") or "").strip().lower()
    continue_url = (step_data.get("continue_url") or "").strip()
    _log(f"[2/6] 邮箱已提交: page_type={page_type or 'normal'}")

    authenticated = False

    # 密码分支（如果账号在注册时设置了密码）
    if page_type == "login_password" or "/log-in/password" in continue_url:
        password = str(account_info.get("password") or "").strip()
        if password:
            _log("正在提交密码进行登录验证...")
            try:
                session.get(f"https://auth.openai.com/log-in/password?email={quote(email)}", headers=nav_headers, timeout=timeout)
            except Exception:
                pass
            pw_headers = dict(post_headers)
            pw_headers["Referer"] = "https://auth.openai.com/log-in/password"
            pw_resp = session.post(
                "https://auth.openai.com/api/accounts/password/verify",
                headers=pw_headers,
                json={"password": password},
                allow_redirects=False,
                timeout=timeout,
            )
            step_data = pw_resp.json() if pw_resp.status_code == 200 else {}
            page_type = ((step_data.get("page") or {}).get("type") or "").strip().lower()
            continue_url = (step_data.get("continue_url") or "").strip()
            _log(f"[2/6] 密码验证响应: status={pw_resp.status_code}, page_type={page_type or 'none'}")
            if pw_resp.status_code == 200 and not page_type:
                authenticated = True

    # 2FA TOTP 分支
    if page_type == "mfa_challenge" or "/mfa-challenge" in continue_url:
        totp_secret = str(account_info.get("totp_secret") or "").strip()
        if totp_secret:
            challenge_id = continue_url.split("/")[-1] if "/mfa-challenge/" in continue_url else ""
            totp_code = _totp_now(totp_secret)
            _log(f"正在提交 2FA TOTP 验证码: {totp_code} (challenge_id={challenge_id[:8]}...)...")
            mfa_headers = dict(post_headers)
            mfa_headers["Referer"] = f"https://auth.openai.com/mfa-challenge/{challenge_id}" if challenge_id else "https://auth.openai.com/mfa-challenge"
            mfa_resp = session.post(
                "https://auth.openai.com/api/accounts/mfa/verify",
                headers=mfa_headers,
                json={"code": totp_code, "type": "totp", "id": challenge_id},
                allow_redirects=False,
                timeout=timeout,
            )
            step_data = mfa_resp.json() if mfa_resp.status_code == 200 else {}
            page_type = ((step_data.get("page") or {}).get("type") or "").strip().lower()
            continue_url = (step_data.get("continue_url") or "").strip()
            if mfa_resp.status_code == 200:
                _log(f"[2/6] ✅ 2FA TOTP 验证通过 (page_type={page_type or 'success'})")
                authenticated = True
            else:
                _log(f"[2/6] ⚠️ 2FA TOTP 验证响应: HTTP {mfa_resp.status_code} - {(mfa_resp.text or '')[:120]}")

    # ──────────────── 阶段 3 & 4: 邮箱 OTP 取码与核验 ────────────────
    need_otp = False
    if not authenticated:
        need_otp = (page_type in ("email_otp_verification", "passwordless_signup", "passwordless_login")) or ("/email-verification" in continue_url) or (not page_type and not continue_url)
    elif "/email-verification" in continue_url or page_type == "email_otp_verification":
        need_otp = True

    if need_otp:
        if not mail_provider:
            email_lower = (email or "").strip().lower()
            if any(dom in email_lower for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
                raise RuntimeError(
                    f"账号 {email} 需要收取邮箱 OTP 验证码，但号池中未找到该微软邮箱的取件凭证 (email----password----client_id----refresh_token)"
                )
            raise RuntimeError(f"账号 {email} 需要收取邮箱验证码，但未配置可用邮箱服务或未获取到取件凭证")

        _step("3", "[3/6] 取邮箱OTP (收信中...)")
        _log("[3/6] 服务端已自动下发验证码邮件，正在等待收件 (timeout=60s) ...")
        t_otp0 = time.time()
        otp_code = mail_provider.wait_for_otp(email, timeout=60, issued_after=otp_issued_after)
        t_otp = round(time.time() - t_otp0, 1)
        if not otp_code:
            raise RuntimeError(f"收取邮箱 OTP 验证码超时 ({t_otp}s) 或未收到邮件")
        _log(f"[3/6] 成功收取到邮箱 OTP 验证码: {otp_code} (耗时 {t_otp}s)")

        _step("4", "[4/6] 校验OTP (验证码核验)")
        _log(f"[4/6] 正在提交验证 OTP: {otp_code} ...")
        st_token_v, so_token_v = "", ""
        try:
            st_token_v, so_token_v = get_sentinel_token(
                session,
                device_id=device_id,
                flow="authorize_continue",
                user_agent=ua,
                lang_full=lang_full,
            )
        except Exception:
            pass

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
        if v_resp.status_code != 200:
            raise RuntimeError(f"邮箱 OTP 验证失败: HTTP {v_resp.status_code} - {(v_resp.text or '')[:150]}")
        _log("[4/6] ✅ 邮箱 OTP 验证成功通过")
        step_data = v_resp.json() if v_resp.status_code == 200 else {}
        page_type = ((step_data.get("page") or {}).get("type") or "").strip().lower()
        continue_url = (step_data.get("continue_url") or "").strip()
    else:
        _log("[3/6] 当前账号已通过密码/2FA 鉴权，跳过邮箱 OTP 取码步骤")

    # 检测是否命中手机号验证
    if page_type == "add_phone" or "/add-phone" in continue_url or "/phone-verification" in continue_url:
        sms_cfg = account_info.get("sms_config") or {}
        sms_enabled = bool(sms_cfg.get("sms_enabled", False))

        if not sms_enabled or skip_sms:
            _log("检测到需要手机号验证 (未开启 SMS 接码，已按要求跳过接码并标记)")
            return {
                "status": "need_phone",
                "label": "需接码(已跳过)",
                "error": "OpenAI 要求绑定手机号 (已跳过)",
            }

        # ── 开启接码：执行 SmsBower 自动租号、发码、收码与验证 ──
        from sms_provider import PhoneCallbackController, parse_price_spec

        provider_key = str(sms_cfg.get("sms_provider") or "smsbower").strip().lower()
        api_key = str(sms_cfg.get("sms_api_key") or "").strip()
        country = str(sms_cfg.get("sms_country") or "52").strip()
        max_price_raw = sms_cfg.get("sms_max_price") or sms_cfg.get("sms_price")
        min_p, max_p, exact_p = parse_price_spec(max_price_raw)
        max_attempts = max(1, min(10, int(sms_cfg.get("sms_max_attempts") or 3)))
        per_phone_timeout = max(20, int(sms_cfg.get("sms_timeout") or 80))

        # 候选国家列表
        if country == "AUTO":
            allowed_countries = "52,6,10,73,15,16,12"
            auto_select_country = True
            primary_country = "52"
        else:
            allowed_countries = f"{country},6,10,73,15,16"
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

        _step("5_sms", f"[5/6] 手机号接码 ({provider_key})")
        _log(f"[5/6] 遇到手机验证，已启用 {provider_key} 接码 (国家={country}, 金额要求={price_desc}, 最多换号={max_attempts}次, 超时={per_phone_timeout}s)...")

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
                "sms_per_phone_timeout": str(per_phone_timeout),
                "sms_max_phone_attempts": str(max_attempts),
                "proxy": proxy or None,
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
                phone_headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": ua,
                    "Accept-Language": lang_full,
                    "Origin": "https://auth.openai.com",
                    "Referer": "https://auth.openai.com/add-phone",
                    "oai-device-id": device_id,
                }
                send_resp = session.post(
                    "https://auth.openai.com/api/accounts/add-phone/send",
                    headers=phone_headers,
                    json={"phone_number": phone, "channel": "sms"},
                    timeout=30,
                )

                if send_resp.status_code != 200:
                    err_msg = (send_resp.text or "")[:150]
                    if "no longer valid" in err_msg.lower() or send_resp.status_code == 409:
                        _log(f"[sms] ❌ OpenAI 登录临时会话过期 (409): {err_msg}，立即取消并释放退款该号码...")
                        ctrl.mark_send_failed("session_expired")
                        raise RuntimeError("OpenAI 登录临时会话过期(409)，请重新发起导出")
                    _log(f"[sms] ❌ OpenAI 拒绝该手机号 ({send_resp.status_code}): {err_msg}，立即取消并释放退款该号码...")
                    ctrl.mark_send_failed(err_msg)  # 释放退款
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

                _log(f"[sms] 🎉 手机号 {phone} 验证成功通过！")
                ctrl.report_success()
                phone_verified = True
                verified_phone = phone
                step_data = val_resp.json() if val_resp.status_code == 200 else {}
                page_type = ((step_data.get("page") or {}).get("type") or "").strip().lower()
                continue_url = (step_data.get("continue_url") or "").strip()
                break
        finally:
            ctrl.cleanup()
            ctrl._release_lock()

        if not phone_verified:
            raise RuntimeError(f"接码失败 (已尝试 {max_attempts} 个号码均已安全退回): {last_sms_err or '未收到短信'}")

    # ──────────────── 阶段 5: 选择工作区与捕获回调 ────────────────
    _step("5", "[5/6] 选工作区 (提取回调)")

    start_candidates = [
        continue_url,
        auth_url.replace("&prompt=login", "").replace("prompt=login&", "").replace("prompt=login", ""),
        auth_url,
    ]

    callback_url = _follow_oauth_callback(
        session=session,
        start_urls=start_candidates,
        redirect_uri=redirect_uri,
        nav_headers=nav_headers,
        post_headers=post_headers,
        device_id=device_id,
        timeout=timeout,
        log_fn=_log,
    )

    if not callback_url or "code=" not in callback_url:
        raise RuntimeError("未能在 OAuth 授权链路中获取到 callback authorization code")

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
    t_resp = session.post(
        "https://auth.openai.com/oauth/token",
        headers=token_headers,
        data=urlencode(token_form),
        timeout=timeout,
    )
    if t_resp.status_code != 200:
        raise RuntimeError(f"换取 token 失败: HTTP {t_resp.status_code} - {(t_resp.text or '')[:180]}")

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
    return {
        "status": "success",
        "label": f"成功 ({plan_type.upper()})",
        "access_token": at,
        "refresh_token": rt,
        "id_token": it,
        "account_id": account_id,
        "plan_type": plan_type,
        "exp_iso": exp_iso,
    }


def _run_one_oauth_export(task: OAuthExportTask, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被中止"})
        return

    task.set_running(email, step_text="[1/6] 建立会话")
    task.add_email_log(email, f"开始重跑 OAuth 导出: {email}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "not_found", "label": "未找到", "error": "数据库中无此凭证记录"}
        task.add_email_log(email, "错误: 数据库中无此凭证记录")
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
    if not mail_account and cred.get("extra"):
        saved_oauth = cred["extra"].get("mail_oauth")
        if isinstance(saved_oauth, dict) and (saved_oauth.get("refresh_token") or saved_oauth.get("password")):
            mail_account = {
                "email": email_lower,
                "password": saved_oauth.get("password", ""),
                "client_id": saved_oauth.get("client_id", ""),
                "refresh_token": saved_oauth.get("refresh_token", ""),
                "kind": saved_oauth.get("kind", "outlook"),
            }

    # 优先根据邮箱域名后缀精准匹配 Provider，绝不让微软邮箱误走 cf_temp
    is_ms = any(dom in email_lower for dom in ("@outlook.", "@hotmail.", "@live.", "@msn."))
    is_icloud = any(dom in email_lower for dom in ("@icloud.", "@me.", "@mac."))

    if is_ms:
        mail_source = "outlook"
    elif is_icloud:
        mail_source = "icloud_relay"
    elif mail_account and mail_account.get("kind"):
        mail_source = str(mail_account.get("kind")).strip().lower()
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
            task.mark_done(email, res)
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

        # 回写数据库 registered 表
        db.update_registered_oauth(
            email=email,
            access_token=at,
            refresh_token=rt,
            id_token=it,
            cookie_header=cred.get("cookie_header") or "",
            extra_data={
                "oauth_export": {
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
        task.add_email_log(email, f"✅ OAuth 导出成功 ({req_ms}ms): RT={len(rt)} 字符, Plan={plan_type}, AccountId={account_id[:8]}...")
        task.mark_done(email, res)

    except Exception as e:
        req_ms = int((time.time() - started_ts) * 1000)
        err_str = str(e)
        if ("add_phone" in err_str.lower() or "手机" in err_str) and not sms_enabled:
            res = {"status": "need_phone", "label": "需接码(已跳过)", "error": "需要手机号验证 (已跳过)", "req_ms": req_ms}
            db.update_registered_oauth_status(email, "need_phone", "需要手机号验证 (已跳过)")
            task.add_email_log(email, "检测到需要手机号验证 (未开启自动接码，已跳过)")
        else:
            is_sms_fail = sms_enabled and ("接码" in err_str or "NO_NUMBERS" in err_str or "短信" in err_str)
            fail_label = "接码失败" if is_sms_fail else "异常失败"
            res = {"status": "error", "label": fail_label, "error": err_str, "req_ms": req_ms}
            db.update_registered_oauth_status(email, "failed", err_str)
            task.add_email_log(email, f"OAuth 执行异常 ({req_ms}ms): {err_str}")
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

