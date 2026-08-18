"""Token 重新获取、快速刷新与凭证导出管理器 (Token Refresh & Re-login Studio)。

核心机制：
  1. 智能双模刷新策略 (Dual-Engine Adaptive Refresh):
     - 策略 A (Fast RT Refresh): 账号已有 refresh_token 时，直接向 auth.openai.com/oauth/token 发起 grant_type=refresh_token 请求，200ms 极速置换新 access_token、id_token 及滚动 refresh_token。
     - 策略 B (Full OAuth Re-login): 若无 RT 或 RT 已失效，自动唤起完整的 Codex OAuth 登录流（使用密码 + TOTP 自动算码 / 邮箱 IMAP 取码），获取全新的全套凭证 (AT / ST / RT / ID Token / Cookies)。
  2. 自动落库：刷新成功后即时回写 SQLite 数据库 (registered 表)，更新凭证与状态时间戳。
  3. 多格式导出与一键下载：
     - TXT 格式 (邮箱----密码----AT----RT----2FA)
     - CPA 格式 JSON (可直接导入 CPA 面板)
     - Sub2API 格式 JSON (可直接导入 Sub2API 面板)
     - Full JSON (完整结构体与 Claims)
  4. 多 Worker 线程池并发调度，支持取消/停止，SSE 实时广播任务进度与单账号日志。
"""
from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:
    CurlSession = None

from . import db
from .proxy_util import (
    COUNTRY_LANG_MAP,
    new_proxy_session_id,
    resolve_target_country,
    route_proxy_country,
)
from .oauth_export import (
    CPA_DIR,
    SUB2_DIR,
    _decode_jwt_payload,
    _get_account_claims,
    _totp_now,
    cpa_credential_to_sub2_account,
    execute_codex_oauth_flow,
)
from mail_providers import create_mail_provider, get_provider_class

logger = logging.getLogger(__name__)

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_SCOPE = "openid email profile offline_access"
OPENAI_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"


def refresh_token_fast(
    refresh_token: str,
    proxy: str = "",
    timeout: float = 25.0,
    client_id: str = CODEX_CLIENT_ID,
) -> dict:
    """使用 refresh_token 极速换取新 access_token / id_token / refresh_token。"""
    rt = str(refresh_token or "").strip()
    if not rt:
        raise ValueError("缺少 refresh_token")

    from http_client import create_http_session

    session = create_http_session(proxy=proxy or None, impersonate="chrome110")
    body = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": rt,
        "scope": CODEX_SCOPE,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Origin": "https://auth.openai.com",
        "Referer": "https://auth.openai.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    }

    resp = session.post(
        OPENAI_TOKEN_ENDPOINT,
        headers=headers,
        data=body,
        timeout=timeout,
    )

    if resp.status_code != 200:
        err_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
        raise RuntimeError(f"RT 换取 Token 失败 (HTTP {resp.status_code}): {err_msg}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("OpenAI 返回非 JSON 数据")

    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OpenAI 响应中未包含 access_token")

    return data


def refresh_session_token_fast(
    session_token: str = "",
    cookie_header: str = "",
    proxy: str = "",
    timeout: float = 25.0,
) -> dict:
    """使用 session_token 快速请求 chatgpt.com/api/auth/session 刷新 access_token。"""
    from http_client import create_http_session
    st = str(session_token or "").strip()
    session = create_http_session(proxy=proxy or None, impersonate="chrome136")

    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="136", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    elif st:
        headers["Cookie"] = f"__Secure-next-auth.session-token={st}"

    resp = session.get("https://chatgpt.com/api/auth/session", headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Session 响应状态码 HTTP {resp.status_code}")

    data = resp.json() or {}
    new_at = data.get("accessToken") or ""
    if not new_at:
        raise RuntimeError("Session 响应未包含 accessToken")

    new_st = session.cookies.get("__Secure-next-auth.session-token", "") or st
    return {
        "access_token": new_at,
        "session_token": new_st,
        "user": data.get("user") or {},
        "expires": data.get("expires") or "",
    }


def execute_token_refresh_flow(
    email: str,
    mail_provider: Any,
    proxy: str = "",
    target_country: str = "",
    account_info: Optional[dict] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    step_fn: Optional[Callable[[str, str], None]] = None,
    force_full_login: bool = False,
    timeout: float = 45.0,
) -> dict:
    """执行单个账号的 Token 刷新或重新获取流程。

    优先级：
      1. 优先尝试 RT 快速置换 (Fast RT Refresh)
      2. 尝试 Session Token 极速刷新 (Fast ST Refresh)
      3. RT / ST 失效时，自动使用 ChatGPT Web 官方协议登录重获 (密码 + 2FA TOTP 自动算码 / 邮箱取码，无需手机接码)
    """
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    def _step(step_key: str, step_text: str):
        if step_fn:
            step_fn(step_key, step_text)

    account_info = account_info or {}
    existing_rt = str(account_info.get("refresh_token") or "").strip()
    existing_st = str(account_info.get("session_token") or "").strip()
    existing_cookies = str(account_info.get("cookie_header") or "").strip()

    # ──────────────── 阶段 1: 尝试 RT 快速刷新 ────────────────
    if existing_rt and not force_full_login:
        _step("rt_fast", "[1/2] 正在尝试使用 Refresh Token 快速置换...")
        _log(f"检测到存在历史 Refresh Token (len={len(existing_rt)})，优先发起极速置换...")
        try:
            t0 = time.time()
            data = refresh_token_fast(existing_rt, proxy=proxy, timeout=min(20.0, timeout))
            elapsed_ms = int((time.time() - t0) * 1000)

            new_at = data.get("access_token") or ""
            new_rt = data.get("refresh_token") or existing_rt
            new_it = data.get("id_token") or ""

            claims = _get_account_claims(new_at)
            _log(f"✅ [RT 极速置换成功] 耗时 {elapsed_ms}ms, access_token 长度={len(new_at)}, 有效期至={claims.get('exp_iso') or '未知'}")

            # 构造 CPA 凭证
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
                "refresh_method": "rt_fast",
            }
            sub2_doc = cpa_credential_to_sub2_account(cpa_doc)

            # 写库更新
            db.update_registered_oauth(
                email=email,
                access_token=new_at,
                refresh_token=new_rt,
                id_token=new_it,
                cookie_header=account_info.get("cookie_header") or "",
                extra_data={
                    "oauth_export": {
                        "status": "success",
                        "updated_at": time.time(),
                        "method": "rt_fast",
                        "claims": claims,
                    }
                },
            )

            return {
                "status": "success",
                "method": "rt_fast",
                "label": "✅ RT极速刷新成功",
                "access_token_len": len(new_at),
                "refresh_token_len": len(new_rt),
                "expires_at": claims.get("exp_iso"),
                "plan_type": claims.get("plan_type") or "free",
                "cpa": cpa_doc,
                "sub2api": sub2_doc,
            }
        except Exception as e:
            _log(f"⚠️ Refresh Token 置换未成功 ({e})，尝试后续刷新策略...")

    # ──────────────── 阶段 2: 尝试 Session Token 极速刷新 ────────────────
    if (existing_st or existing_cookies) and not force_full_login:
        _step("st_fast", "[1/2] 正在尝试使用 Session Token 极速刷新...")
        _log("尝试通过 ChatGPT Web Session 接口直接刷新 Access Token...")
        try:
            t0 = time.time()
            s_data = refresh_session_token_fast(
                session_token=existing_st,
                cookie_header=existing_cookies,
                proxy=proxy,
                timeout=min(20.0, timeout),
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            new_at = s_data.get("access_token") or ""
            new_st = s_data.get("session_token") or existing_st

            claims = _get_account_claims(new_at)
            _log(f"🎉 [Session 极速刷新成功] 耗时 {elapsed_ms}ms, access_token 长度={len(new_at)}, 用户={s_data.get('user', {}).get('email') or email}")

            cpa_doc = {
                "access_token": new_at,
                "refresh_token": existing_rt,
                "session_token": new_st,
                "email": email,
                "name": claims.get("name") or s_data.get("user", {}).get("name") or "",
                "user_id": claims.get("user_id") or s_data.get("user", {}).get("id") or "",
                "account_id": claims.get("account_id") or "",
                "plan_type": claims.get("plan_type") or "free",
                "expires_at": claims.get("exp_iso") or s_data.get("expires"),
                "token_type": "Bearer",
                "last_refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "refresh_method": "st_fast",
            }
            sub2_doc = cpa_credential_to_sub2_account(cpa_doc)

            db.update_registered_oauth(
                email=email,
                access_token=new_at,
                refresh_token=existing_rt,
                session_token=new_st,
                cookie_header=existing_cookies,
                extra_data={
                    "oauth_export": {
                        "status": "success",
                        "updated_at": time.time(),
                        "method": "st_fast",
                        "claims": claims,
                    }
                },
            )

            return {
                "status": "success",
                "method": "st_fast",
                "label": "✅ Session极速刷新成功",
                "access_token_len": len(new_at),
                "refresh_token_len": len(existing_rt),
                "expires_at": claims.get("exp_iso"),
                "plan_type": claims.get("plan_type") or "free",
                "cpa": cpa_doc,
                "sub2api": sub2_doc,
            }
        except Exception as e:
            _log(f"⚠️ Session Token 刷新提示: {e}，自动转入官方 Web 协议登录重获...")

    # ──────────────── 阶段 3: 官方 Web 协议登录重登 ────────────────
    _step("full_oauth", "[2/2] 启动官方 Web 登录流程重新获取全套凭证...")
    _log("正在启动 ChatGPT 官方 Web 认证流程重新登录 (密码 + 2FA TOTP 自动算码)...")

    from config import Config
    from auth_flow import AuthFlow

    cfg = Config()
    cfg.proxy = proxy or None
    env_overrides = {
        "TARGET_COUNTRY": target_country,
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

    result = login_flow.run_protocol_login(
        mail_provider=mail_provider,
        email=email,
        password=account_info.get("password") or "",
    )

    if not result or not (result.access_token or result.session_token or result.refresh_token):
        raise RuntimeError("登录完成，但未获取到有效 Access Token 或 Session Token")

    new_at = result.access_token or ""
    new_rt = result.refresh_token or existing_rt
    new_st = result.session_token or ""
    new_it = result.id_token or ""

    claims = _get_account_claims(new_at)
    cpa_doc = {
        "access_token": new_at,
        "refresh_token": new_rt,
        "id_token": new_it,
        "session_token": new_st,
        "email": email,
        "name": claims.get("name") or "",
        "user_id": claims.get("user_id") or "",
        "account_id": claims.get("account_id") or "",
        "plan_type": claims.get("plan_type") or "free",
        "expires_at": claims.get("exp_iso"),
        "token_type": "Bearer",
        "last_refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "refresh_method": "full_login",
    }
    sub2_doc = cpa_credential_to_sub2_account(cpa_doc)

    db.update_registered_oauth(
        email=email,
        access_token=new_at,
        refresh_token=new_rt,
        session_token=new_st,
        id_token=new_it,
        cookie_header=result.cookie_header or "",
        extra_data={
            "oauth_export": {
                "status": "success",
                "updated_at": time.time(),
                "method": "full_login",
                "claims": claims,
            }
        },
    )

    _log(f"🎉 [Web 登录重获成功] access_token(len={len(new_at)}), session_token(len={len(new_st)}) 已自动更新落库")
    return {
        "status": "success",
        "method": "full_login",
        "label": "✅ Web重登成功",
        "access_token_len": len(new_at),
        "refresh_token_len": len(new_rt),
        "expires_at": claims.get("exp_iso"),
        "plan_type": claims.get("plan_type") or "free",
        "cpa": cpa_doc,
        "sub2api": sub2_doc,
    }


class TokenRefreshTask:
    """批量 Token 刷新/重获任务。"""

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
                "step_text": "待刷新",
                "method": "auto",
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
            "rt_fast_ok": 0,
            "full_login_ok": 0,
            "need_phone": 0,
            "error": 0,
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

    def set_running(self, email: str, step_text: str = "[1/2] 正在刷新 Token...") -> None:
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
            method = result.get("method") or "auto"
            if st == "success":
                self.stats["success"] += 1
                if method == "rt_fast":
                    self.stats["rt_fast_ok"] += 1
                else:
                    self.stats["full_login_ok"] += 1
            elif st == "need_phone":
                self.stats["need_phone"] += 1
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


_tasks: dict[str, TokenRefreshTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _worker_loop(task: TokenRefreshTask, email: str):
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被取消"})
        return

    task.set_running(email, "正在准备刷新...")
    task.add_email_log(email, "▶ 启动 Token 刷新/重获任务")

    account = db.get_account(email) or {"email": email}
    registered_cred = db.get_registered(email) or {}

    # 1. 邮箱底层提供商凭证（严格保留号池原本的邮箱密码、MS client_id、MS refresh_token）
    mail_account_info = {
        "email": email,
        "password": account.get("password") or "",
        "client_id": account.get("client_id") or "",
        "refresh_token": account.get("refresh_token") or "",
        "relay_url": account.get("relay_url") or "",
        "kind": account.get("kind") or "outlook",
    }

    # 2. GPT 认证信息（GPT 密码、OpenAI 2FA TOTP、OpenAI Refresh Token）
    openai_cred_info = {
        "email": email,
        "password": registered_cred.get("password") or account.get("password") or "",
        "totp_secret": registered_cred.get("totp_secret") or "",
        "totp_factor_id": registered_cred.get("totp_factor_id") or "",
        "access_token": registered_cred.get("access_token") or "",
        "refresh_token": registered_cred.get("refresh_token") or "",
        "device_id": registered_cred.get("device_id") or "",
        "cookie_header": registered_cred.get("cookie_header") or "",
        "reg_country": registered_cred.get("reg_country") or "JP",
        "sms_config": {
            "sms_enabled": task.config.get("sms_enabled", False),
            "sms_provider": task.config.get("sms_provider") or "smsbower",
            "sms_api_key": task.config.get("sms_api_key") or "",
            "sms_country": task.config.get("sms_country") or "52",
            "sms_max_price": task.config.get("sms_max_price") or "",
            "sms_max_attempts": task.config.get("sms_max_attempts") or 3,
            "sms_timeout": task.config.get("sms_timeout") or 80,
        },
    }

    raw_proxy = (task.next_proxy() or task.config.get("proxy") or "").strip()
    raw_country = (task.config.get("proxy_country") or openai_cred_info.get("reg_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country) or "JP"
    proxy = raw_proxy

    if raw_proxy and target_country:
        proxy = route_proxy_country(raw_proxy, target_country, new_proxy_session_id())

    # 自动识别邮箱提供商渠道
    mail_source = (mail_account_info.get("kind") or db.get_setting("mail_source", "") or "").strip().lower()
    if not mail_source or mail_source not in ("outlook", "cf_temp", "icloud_relay"):
        if any(dom in email for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            mail_source = "outlook"
        elif any(dom in email for dom in ("@icloud.", "@me.", "@mac.")):
            mail_source = "icloud_relay"
        else:
            mail_source = "cf_temp"

    try:
        mail_provider = create_mail_provider(mail_source, db.get_mail_settings(), mail_account_info)
    except Exception as e:
        task.add_email_log(email, f"邮箱 Provider ({mail_source}) 初始化提示: {e}")
        mail_provider = None

    timeout = float(task.config.get("timeout") or 45.0)
    force_full_login = bool(task.config.get("force_full_login", False))

    try:
        res = execute_token_refresh_flow(
            email=email,
            mail_provider=mail_provider,
            proxy=proxy,
            target_country=target_country,
            account_info=openai_cred_info,
            log_fn=lambda msg: task.add_email_log(email, msg),
            step_fn=lambda k, t: task.set_step(email, k, t),
            force_full_login=force_full_login,
            timeout=timeout,
        )
        task.mark_done(email, res)
    except Exception as e:
        logger.exception(f"[{email}] Token 刷新异常: {e}")
        task.add_email_log(email, f"❌ 任务失败: {e}")
        task.mark_done(email, {
            "status": "error",
            "label": "刷新失败",
            "error": str(e),
        })


def start_token_refresh_task(
    emails: list[str],
    proxies: str = "",
    proxy: str = "",
    proxy_country: str = "",
    workers: int = 5,
    timeout: int = 45,
    force_full_login: bool = False,
    sms_enabled: bool = False,
    sms_provider: str = "smsbower",
    sms_api_key: str = "",
    sms_country: str = "52",
    sms_max_price: str = "",
    sms_max_attempts: int = 3,
    sms_timeout: int = 80,
) -> str:
    """启动并发 Token 刷新任务，返回 taskId。"""
    cleaned_emails = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned_emails:
        raise ValueError("邮箱列表为空")

    proxy_list = [p.strip() for p in proxies.splitlines() if p.strip() and not p.strip().startswith("#")]

    config = {
        "proxies": proxy_list,
        "proxy": proxy.strip(),
        "proxy_country": proxy_country.strip().upper(),
        "workers": max(1, min(20, int(workers or 5))),
        "timeout": max(10, min(120, int(timeout or 45))),
        "force_full_login": force_full_login,
        "sms_enabled": sms_enabled,
        "sms_provider": sms_provider,
        "sms_api_key": sms_api_key,
        "sms_country": sms_country,
        "sms_max_price": sms_max_price,
        "sms_max_attempts": sms_max_attempts,
        "sms_timeout": sms_timeout,
    }

    task_id = f"refresh_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    task = TokenRefreshTask(task_id, cleaned_emails, config)

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    def _runner():
        max_w = config["workers"]
        with ThreadPoolExecutor(max_workers=max_w) as executor:
            for email in cleaned_emails:
                if task.cancelled:
                    break
                executor.submit(_worker_loop, task, email)
        task.finished_at = time.time()
        task.queue.put({"kind": "end", "task_id": task_id})

    threading.Thread(target=_runner, daemon=True, name=f"token-refresh-{task_id}").start()
    return task_id


def stop_token_refresh_task(task_id: str) -> bool:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return False
        task.cancelled = True
        return True


def get_token_refresh_task(task_id: str) -> Optional[TokenRefreshTask]:
    with _tasks_lock:
        return _tasks.get(task_id)


def get_token_refresh_log(task_id: str, email: str) -> list[str]:
    task = get_token_refresh_task(task_id)
    if not task:
        return []
    with task._lock:
        it = task.items.get(email.lower().strip())
        return list(it.get("logs") or []) if it else []


def export_refreshed_tokens_text(task_id: str) -> str:
    """生成 TXT 格式凭证文本 (邮箱----密码----AT----RT----2FA)。"""
    task = get_token_refresh_task(task_id)
    if not task:
        return ""
    lines = []
    with task._lock:
        for email, it in task.items.items():
            if it.get("status") == "done" and it.get("result", {}).get("status") == "success":
                cred = db.get_registered(email) or {}
                pw = cred.get("password") or ""
                at = cred.get("access_token") or ""
                rt = cred.get("refresh_token") or ""
                tfa = cred.get("totp_secret") or ""
                lines.append(f"{email}----{pw}----{at}----{rt}----{tfa}")
    return "\n".join(lines)


def export_refreshed_tokens_cpa_json(task_id: str) -> list[dict]:
    task = get_token_refresh_task(task_id)
    if not task:
        return []
    out = []
    with task._lock:
        for it in task.items.values():
            if it.get("cpa"):
                out.append(it["cpa"])
    return out


def export_refreshed_tokens_sub2api_json(task_id: str) -> dict:
    task = get_token_refresh_task(task_id)
    if not task:
        return {"accounts": []}
    accounts = []
    with task._lock:
        for it in task.items.values():
            if it.get("sub2api"):
                accounts.append(it["sub2api"])
    return {"accounts": accounts}
