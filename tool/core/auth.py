"""auth.py — OpenAI 账密 + TOTP 2FA 登录、官方免邮箱改密与 Sub2API 提取核心状态机
=======================================================================================
深度对接官方 OAuth 授权与 2FA 验证链路，支持任意已有 GPT 账号通过账密 + 2FA 密钥：
  1. 全自动完成 2FA 登录建立 Cloudflare 信任会话；
  2. 官方免邮箱 Sudo 重认证链路（旧密码 -> 2FA验证 -> 官方 password/reset 接口直接生效）；
  3. 提取 1900+ 字符 AccessToken 以及 Codex 永久 RefreshToken (RT) 和 IDToken；
  4. 生成标准 Sub2API 导入 JSON。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import secrets
import string
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

# 确保能从上级或当前目录导入
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from .exporter import decode_jwt_payload
from .proxy_util import route_proxy_for_worker
from .sentinel import get_sentinel_token
from .totp import get_totp_token

logger = logging.getLogger("auth")

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def generate_random_password(length: int = 16) -> str:
    """生成符合 OpenAI 强度规范的高安全性随机密码（大小写+数字+特殊符号）。"""
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    punct = random.choice("@#$%^&*!_+=")
    all_chars = string.ascii_letters + string.digits + "@#$%^&*!_+="
    rest = [random.choice(all_chars) for _ in range(length - 4)]
    combo = list(lower + upper + digit + punct + "".join(rest))
    random.shuffle(combo)
    return "".join(combo)


class AccountAuthWorker:
    """单个账号的处理执行器（包含登录、2FA、官方改密、提取Token）。"""

    def __init__(
        self,
        email: str,
        password: str,
        totp_secret: str = "",
        new_password_mode: str = "keep",  # "keep" / "random" / "custom" / "prefix" / "inline"
        custom_password: str = "",
        password_prefix: str = "Gpt@",
        proxy: str = "",
        timeout: int = 50,
        log_cb: Optional[Callable[[str], None]] = None,
        step_cb: Optional[Callable[[str], None]] = None,
    ):
        self.email = (email or "").strip().lower()
        self.old_password = (password or "").strip()
        self.totp_secret = (totp_secret or "").strip()
        self.new_password_mode = new_password_mode
        self.custom_password = custom_password
        self.password_prefix = password_prefix
        self.proxy = (proxy or "").strip()
        self.timeout = timeout
        self.log_cb = log_cb
        self.step_cb = step_cb
        self.logs: list[dict] = []

    def _log(self, msg: str, level: str = "info"):
        t_str = time.strftime("%H:%M:%S")
        entry = {"time": t_str, "message": msg, "level": level}
        self.logs.append(entry)
        logger.info(f"[{self.email}] {msg}")
        if self.log_cb:
            try:
                self.log_cb(msg)
            except Exception:
                pass

    def _step(self, step_name: str):
        if self.step_cb:
            try:
                self.step_cb(step_name)
            except Exception:
                pass

    def _determine_new_password(self, inline_pwd: str = "") -> str:
        if self.new_password_mode == "keep":
            return self.old_password
        if self.new_password_mode == "inline" and inline_pwd:
            return inline_pwd
        if self.new_password_mode == "custom" and self.custom_password:
            return self.custom_password
        if self.new_password_mode == "prefix":
            prefix = self.password_prefix or "Gpt@"
            return f"{prefix}{generate_random_password(10)}"
        return generate_random_password(16)

    def process(self, inline_new_pwd: str = "") -> dict:
        """端到端执行全流程：账密+2FA登录 -> 官方免邮箱改密 -> 换取完整 Codex OAuth 凭证。"""
        t0 = time.time()
        self._step("正在初始化浏览器指纹与网络会话...")
        self._log("开始建立 Cloudflare 信任网络会话...")

        target_new_password = self._determine_new_password(inline_new_pwd)
        want_change_pwd = (self.new_password_mode != "keep" and target_new_password and target_new_password != self.old_password)

        from auth_flow import AuthFlow
        from config import Config

        cfg = Config()
        if self.proxy:
            cfg.proxy = self.proxy

        flow = AuthFlow(
            cfg,
            account_callback=lambda em: {"password": self.old_password, "totp_secret": self.totp_secret},
        )

        # 1. Warmup 与 Cloudflare 信任态建立
        try:
            self._step("正在建立 Cloudflare 信任态...")
            flow.warmup()
        except Exception as e:
            self._log(f"Warmup 提示 (非致命): {e}")

        # 2. 获取登录 CSRF Token
        self._step("正在获取登录 CSRF 会话...")
        csrf_token = flow.get_csrf_token()
        if not csrf_token:
            raise RuntimeError("获取登录 CSRF Token 失败，节点可能受限")

        # 3. 初始化 OAuth 会话
        self._step("正在建立 OpenAI 官方鉴权会话...")
        auth_url = flow.get_auth_url(csrf_token, email=self.email)
        device_id = flow.auth_oauth_init(auth_url)
        self._log(f"授权会话建立成功: device_id={device_id[:10]}...")

        # 4. 求解 Sentinel PoW
        self._step("正在计算 Sentinel PoW 挑战...")
        sentinel_token = flow.get_sentinel_token(device_id)

        # 5. 提交邮箱
        self._step("正在提交账号邮箱...")
        self._log(f"提交邮箱: {self.email}")
        step_data = flow.authorize_continue(email=self.email, sentinel_token=sentinel_token, screen_hint="login")
        self._log("邮箱受理成功，准备校验当前登录密码...")

        # 6. 提交当前旧密码
        self._step("正在提交当前登录密码...")
        self._log("正在提交密码验证...")
        step_pw = flow.login_password_verify(self.old_password)
        pt = flow._extract_page_type(step_pw)
        cu = flow._normalize_continue_url(flow._extract_continue_url_from_step(step_pw))

        # 7. 提交 2FA TOTP
        callback_url = cu
        if flow._is_mfa_challenge_state(pt, cu):
            self._step("正在计算并提交 2FA 动态码...")
            if not self.totp_secret:
                raise RuntimeError("账号已开启 2FA 两步验证，但未提供 2FA 密钥 (totp_secret)")

            totp_code = get_totp_token(self.totp_secret)
            if not totp_code:
                raise RuntimeError("2FA 密钥无效，无法计算当前 6 位验证码")

            challenge_id = cu.split("/")[-1] if "/mfa-challenge/" in cu else ""
            self._log(f"提交 2FA TOTP={totp_code} (Challenge ID: {challenge_id[:8] or '无'}...)")

            mfa_resp = flow.submit_mfa_totp(totp_code, challenge_id)
            callback_url = mfa_resp.get("continue_url")
            self._log("✅ 2FA 动态码校验成功通过！")

        if "/add-phone" in (callback_url or ""):
            raise RuntimeError("账号通过 2FA 验证，但官方要求绑定手机号 (add-phone)")

        # 8. 跟随 Callback 建立 ChatGPT Web 基础会话
        self._step("正在建立 ChatGPT 登录态...")
        if callback_url:
            flow.session.get(callback_url, allow_redirects=True, timeout=self.timeout)

        # ──────────────────────── 官方免邮箱改密链路 (Sudo Flow) ────────────────────────
        effective_password = self.old_password

        if want_change_pwd:
            self._step(f"正在向 OpenAI 官方申请免邮箱改密 (新密码: {target_new_password})...")
            self._log(f"发起官方免邮箱改密重认证 (目标新密码: {target_new_password})...")

            try:
                # 8.1 从已登录 ChatGPT 会话生成改密重认证入口
                csrf2 = flow.get_csrf_token()
                r_signin = flow.session.post(
                    "https://chatgpt.com/api/auth/signin/openai",
                    data={
                        "csrfToken": csrf2,
                        "callbackUrl": "https://chatgpt.com/#settings/Security",
                        "json": "true",
                    },
                    headers={"Referer": "https://chatgpt.com/#settings/Security"},
                    timeout=self.timeout,
                )
                chg_auth_url = (r_signin.json() or {}).get("url") or ""
                if not chg_auth_url:
                    raise RuntimeError("获取官方改密授权入口失败")

                if "post_login_password_reset" not in chg_auth_url:
                    chg_auth_url += "&post_login_password_reset=true"

                # 8.2 初始化改密会话
                chg_did = flow.auth_oauth_init(chg_auth_url)
                chg_sen = flow.get_sentinel_token(chg_did)

                # 8.3 提交邮箱推进至 log-in/password
                flow.authorize_continue(email=self.email, sentinel_token=chg_sen, screen_hint="login")

                # 8.4 提交当前旧密码
                chg_step_pw = flow.login_password_verify(self.old_password)
                cu_pw = flow._normalize_continue_url(flow._extract_continue_url_from_step(chg_step_pw))
                cid_pw = cu_pw.split("/")[-1] if "/mfa-challenge/" in cu_pw else ""

                # 8.5 提交 2FA 验证，直接到达 reset-password/new-password
                totp_code2 = get_totp_token(self.totp_secret)
                mfa_chg_resp = flow.submit_mfa_totp(totp_code2, cid_pw)
                chg_page_type = str((mfa_chg_resp.get("page") or {}).get("type") or "")
                self._log(f"重认证 2FA 校验通过，进入改密页面: {chg_page_type or 'new-password'}")

                # 8.6 计算 password_reset 专用 Sentinel 挑战
                self._step("正在计算 password_reset PoW 并提交新密码...")
                sen_reset, sen_so = get_sentinel_token(
                    flow.session,
                    device_id=chg_did,
                    flow="password_reset",
                )

                # 8.7 向官方接口提交新密码
                reset_headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": "https://auth.openai.com",
                    "Referer": "https://auth.openai.com/reset-password/new-password",
                    "oai-device-id": chg_did,
                }
                if sen_reset:
                    reset_headers["openai-sentinel-token"] = sen_reset
                if sen_so:
                    reset_headers["openai-sentinel-so-token"] = sen_so

                r_reset = flow.session.post(
                    "https://auth.openai.com/api/accounts/password/reset",
                    headers=reset_headers,
                    json={"password": target_new_password},
                    timeout=self.timeout,
                )

                if r_reset.status_code != 200:
                    err_txt = (r_reset.text or "")[:150]
                    raise RuntimeError(f"官方提交新密码失败 ({r_reset.status_code}): {err_txt}")

                # 8.8 跟随改密后 callback 重建登录态
                reset_data = r_reset.json() or {}
                cb_after_reset = reset_data.get("continue_url") or ""
                if cb_after_reset:
                    flow.session.get(cb_after_reset, allow_redirects=True, timeout=self.timeout)

                effective_password = target_new_password
                self._log(f"🎉【官方改密成功】新密码 {target_new_password} 已在 OpenAI 服务端正式生效！", level="success")

            except Exception as e:
                self._log(f"❌ 官方改密失败: {e}", level="error")
                raise RuntimeError(f"官方改密失败: {e}")

        # 9. 提取 ChatGPT Session 数据
        self._step("正在提取 ChatGPT 会话凭据...")
        sess_resp = flow.session.get("https://chatgpt.com/api/auth/session", timeout=self.timeout)
        sess_data = sess_resp.json() if sess_resp.status_code == 200 else {}
        access_token = str(sess_data.get("accessToken") or "").strip()

        claims = decode_jwt_payload(access_token)
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        account_id = str(auth_claims.get("chatgpt_account_id") or sess_data.get("account", {}).get("id") or "").strip()
        plan_type = str(auth_claims.get("plan_type") or sess_data.get("account", {}).get("planType") or "free").strip()

        # 10. 二次 Authorize 换取 Codex OAuth Refresh Token (Sub2API 专用)
        self._step("正在换取 Codex 永久 Refresh Token (RT)...")
        self._log("正在发起二次 OAuth 授权以获取 Sub2API 专用 Refresh Token...")
        refresh_token = ""
        id_token = ""

        try:
            verifier = _b64url_no_pad(secrets.token_bytes(64))
            challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
            state = _b64url_no_pad(secrets.token_bytes(24))

            sec_auth_params = {
                "client_id": CODEX_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": CODEX_REDIRECT_URI,
                "scope": CODEX_SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
            }
            sec_auth_url = f"https://auth.openai.com/oauth/authorize?{urlencode(sec_auth_params)}"
            r_sec = flow.session.get(sec_auth_url, allow_redirects=True, timeout=self.timeout)

            html_text = r_sec.text or ""
            sec_code = ""

            m_code = re.search(r"[?&]code=([^&#]+)", r_sec.url or "")
            if m_code:
                sec_code = m_code.group(1)
            else:
                m_sess = re.search(r"us_[A-Za-z0-9]{16,}", html_text)
                if m_sess:
                    session_id = m_sess.group(0)
                    post_headers = {
                        "Origin": "https://auth.openai.com",
                        "Referer": "https://auth.openai.com/choose-an-account",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "oai-device-id": device_id,
                    }
                    resp_sel = flow.session.post(
                        "https://auth.openai.com/api/accounts/session/select",
                        headers=post_headers,
                        json={"session_id": session_id},
                        timeout=self.timeout,
                    )
                    data_sel = resp_sel.json() if resp_sel.status_code == 200 else {}
                    cas = data_sel.get("oai-client-auth-session", {})
                    workspaces = cas.get("workspaces", [])
                    workspace_id = workspaces[0].get("id") if workspaces else account_id

                    if workspace_id:
                        ws_headers = dict(post_headers)
                        ws_headers["Referer"] = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
                        ws_resp = flow.session.post(
                            "https://auth.openai.com/api/accounts/workspace/select",
                            headers=ws_headers,
                            json={"workspace_id": workspace_id},
                            timeout=self.timeout,
                        )
                        ws_data = ws_resp.json() if ws_resp.status_code == 200 else {}
                        final_cb = ws_data.get("continue_url") or ""
                        m_final = re.search(r"[?&]code=([^&#]+)", final_cb)
                        if m_final:
                            sec_code = m_final.group(1)

            if sec_code:
                tok_data = {
                    "grant_type": "authorization_code",
                    "client_id": CODEX_CLIENT_ID,
                    "code": sec_code,
                    "redirect_uri": CODEX_REDIRECT_URI,
                    "code_verifier": verifier,
                }
                tok_res = flow.session.post("https://auth.openai.com/oauth/token", data=tok_data, timeout=self.timeout)
                tok_json = tok_res.json() if tok_res.status_code == 200 else {}
                refresh_token = str(tok_json.get("refresh_token") or "").strip()
                id_token = str(tok_json.get("id_token") or "").strip()
                if tok_json.get("access_token"):
                    access_token = str(tok_json.get("access_token")).strip()
                self._log("✅ 成功换取 Codex 永久 Refresh Token (RT)！")
        except Exception as e:
            self._log(f"Codex RT 换取提示 (非致命): {e}")

        if not access_token:
            raise RuntimeError("未能提取到有效的 Access Token")

        cost_time = round(time.time() - t0, 2)
        self._step("全部操作已完成！")
        self._log(f"🎉 任务成功！耗时: {cost_time}s | 套餐: {plan_type.upper()} | 当前密码: {effective_password} | AT长度: {len(access_token)} | RT长度: {len(refresh_token)} | Sub2API JSON 已就绪")

        return {
            "ok": True,
            "email": self.email,
            "old_password": self.old_password,
            "password": effective_password,
            "new_password": effective_password,
            "password_changed": (effective_password != self.old_password),
            "totp_secret": self.totp_secret,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "account_id": account_id,
            "plan_type": plan_type,
            "cost_time": cost_time,
            "logs": list(self.logs),
        }
