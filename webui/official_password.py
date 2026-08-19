"""official_password.py — OpenAI 官方全自动设置 / 重置密码自动化协议
=====================================================================
通过模拟官方 Password Reset 认证链路，实现无需用户手动干预的
端到端自动化给 GPT 账号在 OpenAI 官方服务端真正设置 / 补设 / 修改登录密码。

核心自动化步骤：
1. 使用 AuthFlow 初始化登录会话（含 warmup 种 cookie 与 Cloudflare 信任态）
2. authorize/continue 定位至 log-in/password 页面
3. 调用 POST https://auth.openai.com/api/accounts/password/send-otp 触发官方发送 6 位重置验证码
4. 自动通过对应邮箱 Provider（Outlook/CF/iCloud）收取验证码
5. 提交 verify_otp 验证通过
6. 调用 POST https://auth.openai.com/api/accounts/password/reset 提交新密码，在官方服务端正式生效
7. 同步持久化写入 SQLite 数据库 registered 表
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth_flow import AuthFlow
from config import Config
from mail_providers import create_mail_provider, extract_otp
from . import db
from .two_factor import generate_random_password

logger = logging.getLogger("official_password")


def official_set_account_password(
    email: str,
    new_password: str = "",
    proxy: str = "",
    timeout: int = 60,
    step_cb: Optional[Callable[[str], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """全自动向 OpenAI 官方申请重置邮件并设置新密码。

    Args:
        email: 目标账号邮箱
        new_password: 目标密码，为空时自动生成 16 位强随机密码
        proxy: 网络代理
        timeout: 收信等待超时时间（秒）
        step_cb: 步骤变更回调
        log_cb: 详细日志回调

    Returns:
        dict: {"ok": True, "email": email, "password": new_password, "official_applied": True, "message": "..."}
    """
    def _log(msg: str):
        logger.info(f"[official-pwd] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def _step(step_name: str):
        if step_cb:
            try:
                step_cb(step_name)
            except Exception:
                pass

    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise ValueError("邮箱不能为空")

    if not new_password:
        new_password = generate_random_password(16)

    # 1. 查找邮箱底层 Provider 凭证
    _step("正在查找并初始化收信渠道...")
    _log(f"开始为 {email_clean} 检索收信渠道凭证...")
    row = db.get_registered(email_clean) or {}
    account_row = db.get_account(email_clean) or {}

    # 若号池无记录，尝试从 registered.extra.mail_oauth 中恢复
    if not account_row and row.get("extra"):
        saved_oauth = row["extra"].get("mail_oauth")
        if isinstance(saved_oauth, dict) and (saved_oauth.get("refresh_token") or saved_oauth.get("password")):
            account_row = {
                "email": email_clean,
                "password": saved_oauth.get("password", ""),
                "client_id": saved_oauth.get("client_id", ""),
                "refresh_token": saved_oauth.get("refresh_token", ""),
                "kind": saved_oauth.get("kind", "outlook"),
            }

    mail_source = (
        (account_row.get("kind") if account_row else None)
        or row.get("kind")
        or db.get_setting("mail_source", "")
        or ""
    ).strip().lower()

    if not mail_source or mail_source not in ("outlook", "cf_temp", "icloud_relay"):
        if any(dom in email_clean for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            mail_source = "outlook"
        elif any(dom in email_clean for dom in ("@icloud.", "@me.", "@mac.")):
            mail_source = "icloud_relay"
        else:
            mail_source = "cf_temp"

    if mail_source == "outlook" and (not account_row or (not account_row.get("refresh_token") and not account_row.get("password"))):
        raise RuntimeError(
            f"未在号池中找到 {email_clean} 的微软 OAuth 凭证或密码。若该账号为外部导入或号池已清空，请先在「号池管理」中导入 4 段式凭证以支持官方自动改密收信。"
        )

    settings = db.get_mail_settings()
    try:
        mail_provider = create_mail_provider(mail_source, settings, account_row)
    except Exception as e:
        if mail_source == "outlook":
            raise RuntimeError(f"初始化 Outlook 邮箱 Provider 异常: {e}")
        _log(f"⚠️ 邮箱 Provider 初始化异常: {e}，回退到 cf_temp")
        mail_provider = create_mail_provider("cf_temp", settings)

    _log(f"已选用收件渠道: {mail_provider.display_name}")

    cfg = Config()
    cfg.proxy = proxy or db.get_setting("proxy", "") or None

    _step("正在初始化官方认证会话 (PoW / Sentinel)...")
    _log(f"正在建立官方 AuthFlow 握手会话 (代理: {cfg.proxy or '直连'})...")
    flow = AuthFlow(cfg)
    flow.warmup()
    csrf = flow.get_csrf_token()
    auth_url = flow.get_auth_url(csrf, email=email_clean)
    dev_id = flow.auth_oauth_init(auth_url)
    st = flow.get_sentinel_token(dev_id)
    step = flow.authorize_continue(email_clean, st, screen_hint="login")

    # 2. 触发 password/send-otp
    _step("向 OpenAI 官方申请发送重置密码邮件...")
    _log("正在向 auth.openai.com/api/accounts/password/send-otp 发起重置发码请求...")
    h = flow._common_headers("https://auth.openai.com/log-in/password")
    h["Content-Type"] = "application/json"
    if st:
        h["openai-sentinel-token"] = st
    if getattr(flow, "_last_sentinel_so_token", ""):
        h["openai-sentinel-so-token"] = flow._last_sentinel_so_token

    t_sent = time.time()
    r_otp = flow.session.post("https://auth.openai.com/api/accounts/password/send-otp", headers=h, json={}, timeout=25)
    if r_otp.status_code != 200:
        err_msg = (r_otp.text or "")[:200]
        _log(f"❌ 向 OpenAI 申请发送验证码失败 (HTTP {r_otp.status_code}): {err_msg}")
        raise RuntimeError(f"向 OpenAI 申请发送重置密码验证码失败 (HTTP {r_otp.status_code}): {err_msg}")

    _step(f"正在从 {mail_provider.display_name} 收取重置 OTP...")
    _log(f"官方已发码，正在轮询邮箱收取重置验证码 (超时 {timeout}s)...")

    # 3. 从邮箱收取验证码（强制要求在发码时间之后产生的新邮件）
    time.sleep(3)
    otp_code = mail_provider.wait_for_otp(email_clean, timeout=timeout, issued_after=t_sent)
    if not otp_code:
        _log("❌ 等待接收 OpenAI 官方重置验证码超时")
        raise RuntimeError(f"等待接收 OpenAI 官方重置验证码超时 ({timeout}s)，未收到邮件")

    _step(f"已获取验证码 {otp_code}，正在提交校验...")
    _log(f"✅ 成功抓取到重置 OTP: {otp_code}，正在进行官方核验...")

    # 4. 校验验证码
    flow.verify_otp(otp_code)
    _log("官方 OTP 核验通过，会话已进入 reset_password 阶段")

    # 5. 提交新密码到 /password/reset
    _step("正在向 OpenAI 官方服务端提交新密码...")
    _log(f"正在向 /api/accounts/password/reset 提交新登录密码...")
    st_p = flow.get_sentinel_token(dev_id)
    h_reset = flow._common_headers("https://auth.openai.com/create-account/password")
    h_reset["Content-Type"] = "application/json"
    if st_p:
        h_reset["openai-sentinel-token"] = st_p

    r_set = flow.session.post(
        "https://auth.openai.com/api/accounts/password/reset",
        headers=h_reset,
        json={"password": new_password},
        timeout=25,
    )
    if r_set.status_code != 200:
        err_text = (r_set.text or "")[:200]
        _log(f"❌ 向官方提交新密码失败 (HTTP {r_set.status_code}): {err_text}")
        raise RuntimeError(f"向 OpenAI 官方提交新密码失败 (HTTP {r_set.status_code}): {err_text}")

    _log(f"🎉 账号 {email_clean} 密码已在 OpenAI 官方服务端成功生效！")

    # 6. 回写本地数据库
    _step("正在更新数据库并落盘...")
    db.update_registered_manual(email_clean, password=new_password)
    _log(f"新密码 {new_password} 已成功回写本地 registered 表")

    return {
        "ok": True,
        "email": email_clean,
        "password": new_password,
        "official_applied": True,
        "message": "密码已在 OpenAI 官方服务端成功生效并持久化到本地数据库",
    }

    # 6. 回写本地数据库
    db.update_registered_manual(email_clean, password=new_password)

    return {
        "ok": True,
        "email": email_clean,
        "password": new_password,
        "official_applied": True,
        "message": "密码已在 OpenAI 官方服务端成功生效并持久化到本地数据库",
    }
