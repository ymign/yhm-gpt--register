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

    is_ms = any(dom in email_clean for dom in ("@outlook.", "@hotmail.", "@live.", "@msn."))
    is_icloud = any(dom in email_clean for dom in ("@icloud.", "@me.", "@mac."))

    if is_ms:
        mail_source = "outlook"
    elif is_icloud:
        mail_source = "icloud_relay"
    elif account_row and account_row.get("kind"):
        mail_source = str(account_row.get("kind")).strip().lower()
    elif row.get("kind"):
        mail_source = str(row.get("kind")).strip().lower()
    else:
        mail_source = (db.get_setting("mail_source", "") or "cf_temp").strip().lower()

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

    _step("正在初始化官方认证会话 (Warmup 握手)...")
    _log(f"正在建立官方 AuthFlow 握手会话 (代理: {cfg.proxy or '直连'})...")
    flow = AuthFlow(cfg)
    flow.warmup()
    csrf = flow.get_csrf_token()
    auth_url = flow.get_auth_url(csrf, email=email_clean)
    dev_id = flow.auth_oauth_init(auth_url)
    flow.result.device_id = dev_id
    flow.result.email = email_clean
    _step("正在计算 Sentinel PoW 安全挑战...")
    _log("正在计算官方 PoW (Proof of Work) 验证令牌...")
    st = flow.get_sentinel_token(dev_id)
    t_sent = time.time()
    _step("正在提交邮箱识别账号认证状态...")
    step_data = flow.authorize_continue(email_clean, st, screen_hint="login")

    page = (step_data.get("page") or {}) if isinstance(step_data, dict) else {}
    page_type = (page.get("type") or "").strip()
    continue_url = (step_data.get("continue_url") or "").strip() if isinstance(step_data, dict) else ""

    is_passwordless = (page_type == "email_otp_verification") or ("/email-verification" in continue_url)

    # 2. 发码/准备收码
    # ── 分支 A：已有密码账号 (login_password)，调用 password/send-otp 请求官方重置邮件
    # ── 分支 B：原生免密账号 (email_otp_verification)，OpenAI 在提交邮箱时已直接下发登录 OTP
    if not is_passwordless:
        _step("向 OpenAI 官方申请发送重置密码邮件...")
        _log("正在向 auth.openai.com/api/accounts/password/send-otp 发起重置发码请求...")
        t_sent = time.time()
        ref = continue_url if (continue_url and "auth.openai.com" in continue_url) else "https://auth.openai.com/log-in/password"
        flow.send_password_reset_otp(referer=ref)
    else:
        _step(f"账号处于免密登录状态，正在从 {mail_provider.display_name} 收取 OTP...")
        _log("检测到账号为 OpenAI 原生免密账号 (Passwordless)，无需官方静态密码，正在执行 OTP + 2FA 验活...")

    _step(f"正在从 {mail_provider.display_name} 收取验证码...")
    _log(f"正在轮询邮箱收取验证码 (超时 {timeout}s)...")

    # 3. 从邮箱收取验证码（强制要求在发码时间之后产生的新邮件）
    time.sleep(1)
    t_start_wait = time.time()
    otp_code = None
    while time.time() - t_start_wait < timeout:
        elapsed = int(time.time() - t_start_wait)
        _step(f"正在从 {mail_provider.display_name} 收信 (已等 {elapsed}s / {timeout}s)...")
        try:
            rem = min(8, int(timeout - (time.time() - t_start_wait)))
            if rem <= 0:
                break
            otp_code = mail_provider.wait_for_otp(email_clean, timeout=rem, issued_after=t_sent - 10)
            if otp_code:
                break
        except TimeoutError:
            pass
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                pass
            else:
                raise

    if not otp_code:
        _log("❌ 等待接收 OpenAI 官方验证码超时")
        raise RuntimeError(f"等待接收 OpenAI 官方验证码超时 ({timeout}s)，未收到邮件")

    _step(f"已获取验证码 {otp_code}，正在提交校验...")
    _log(f"✅ 成功抓取到 OTP: {otp_code}，正在进行官方核验...")

    # 4. 校验验证码
    verify_resp = flow.verify_otp(otp_code)
    _log("官方 OTP 核验通过")

    # 若遇到 2FA TOTP 挑战，自动计算并提交 2FA
    v_page = (verify_resp.get("page") or {}) if isinstance(verify_resp, dict) else {}
    v_type = (v_page.get("type") or "").strip()
    v_continue = (verify_resp.get("continue_url") or "").strip() if isinstance(verify_resp, dict) else ""
    if v_type in ("mfa_challenge", "totp_verification") or "/mfa-challenge" in v_continue:
        reg_info = db.get_registered(email_clean) or {}
        totp_sec = (reg_info.get("totp_secret") or "").strip()
        if not totp_sec and account_row:
            totp_sec = (account_row.get("totp_secret") or "").strip()
        if not totp_sec:
            raise RuntimeError(f"账号 {email_clean} 开启了 2FA 两步验证 (mfa-challenge)，但本地数据库未登记 TOTP 密钥")

        import pyotp
        totp_code = pyotp.TOTP(totp_sec).now()
        challenge_id = v_continue.split("/")[-1] if "/mfa-challenge/" in v_continue else ""
        _log(f"检测到 2FA 挑战，正在提交 TOTP 动态码: {totp_code} (challenge_id={challenge_id[:8] if challenge_id else '无'})...")
        mfa_resp = flow.submit_mfa_totp(totp_code, challenge_id)
        _log("官方 2FA TOTP 校验通过")
        if isinstance(mfa_resp, dict) and mfa_resp.get("continue_url"):
            v_continue = str(mfa_resp["continue_url"]).strip()

    if not is_passwordless:
        # 5. 已有密码账号：提交新密码到 /password/reset
        _step("正在向 OpenAI 官方服务端提交新密码...")
        _log(f"正在提交新登录密码...")
        flow.reset_password_submit(new_password)
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
            "is_passwordless": False,
            "label": "🎉 官方服务端设密成功",
            "message": f"密码已在 OpenAI 官方服务端成功生效 ({new_password}) 并持久化到本地",
        }
    else:
        # 免密账号：跟进跳转链获取/刷新 ChatGPT Session，并将随机密码保存在本地供备用
        _step("免密账号会话就绪，正在刷新官方凭证...")
        try:
            target_start = v_continue or continue_url or "https://auth.openai.com/oauth/authorize"
            if target_start.startswith("/"):
                target_start = f"https://auth.openai.com{target_start}"
            flow.follow_redirect_chain(target_start)
            st_res, at_res = flow.get_auth_session()
            reg_row = db.get_registered(email_clean) or {}
            ex = reg_row.get("extra") or {}
            if flow.result.session_data:
                ex["session_data"] = flow.result.session_data
            if at_res:
                ex["access_token"] = at_res
            db.update_registered_manual(email_clean, password=new_password, extra=ex)
        except Exception as e:
            logger.warning(f"免密账号会话凭证捕获非致命异常: {e}")
            db.update_registered_manual(email_clean, password=new_password)

        _log(f"✅ 账号 {email_clean} 为 OpenAI 原生免密账号 (Passwordless，无需官方静态密码)，全流程验活通过！本地已保存随机密码 ({new_password}) 备用。")
        return {
            "ok": True,
            "email": email_clean,
            "password": new_password,
            "official_applied": False,
            "is_passwordless": True,
            "label": "✅ 官方免密账号 (已验活)",
            "message": f"该账号为 OpenAI 原生免密账号 (Passwordless)，无需官方静态密码。已通过官方 OTP + 2FA 验活并就绪，本地已保存备用密码 ({new_password})。",
        }
