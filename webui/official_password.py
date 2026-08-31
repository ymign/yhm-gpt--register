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
    mail_provider: Optional[MailProvider] = None,
) -> dict:
    """全自动向 OpenAI 官方申请重置邮件并设置新密码（支持已有密码改密及免密账号补设官方密码）。

    Args:
        email: 目标账号邮箱
        new_password: 目标密码，为空时自动生成 16 位强随机密码
        proxy: 网络代理
        timeout: 收信等待超时时间（秒）
        step_cb: 步骤变更回调
        log_cb: 详细日志回调
        mail_provider: 外部已初始化的 MailProvider 实例（可选，注册链路直接传入防丢凭证）

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

    # 1. 查找邮箱底层 Provider 凭证（若外部已传入 mail_provider 则直接复用）
    account_row = {}
    if not mail_provider:
        _step("正在查找并初始化收信渠道...")
        _log(f"开始为 {email_clean} 检索收信渠道凭证...")
        row = db.get_registered(email_clean) or {}
        account_row = db.get_account(email_clean) or {}

        saved_oauth = {}
        if row.get("extra"):
            saved_oauth = row["extra"].get("mail_oauth") or {}
        if not isinstance(saved_oauth, dict):
            saved_oauth = {}

        mail_source = ""
        # 优先使用注册时绑定的 mail_oauth 凭证（涵盖 Remail / 微软 OAuth / iCloud 中转）
        if saved_oauth.get("kind") == "remail" or saved_oauth.get("service_token") or saved_oauth.get("pickup_url"):
            mail_source = "remail"
            account_row = {
                "email": email_clean,
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
            account_row = {
                "email": email_clean,
                "relay_url": saved_oauth.get("relay_url", ""),
                "kind": "icloud_relay",
            }
        elif saved_oauth.get("kind") == "outlook" or saved_oauth.get("refresh_token") or saved_oauth.get("client_id"):
            mail_source = "outlook"
            account_row = {
                "email": email_clean,
                "password": saved_oauth.get("password", ""),
                "client_id": saved_oauth.get("client_id", ""),
                "refresh_token": saved_oauth.get("refresh_token", ""),
                "kind": "outlook",
            }

        # 兜底：如果 registered 没记全，但该邮箱在 remail_recycle_pool 中有记录
        if not mail_source:
            try:
                cur_pool = db._conn().execute(
                    "SELECT service_token, order_no, project_id, email_suffix, service_mode FROM remail_recycle_pool WHERE email=? AND service_token IS NOT NULL AND service_token != '' ORDER BY id DESC LIMIT 1",
                    (email_clean,),
                ).fetchone()
                if cur_pool and cur_pool["service_token"]:
                    mail_source = "remail"
                    account_row = {
                        "email": email_clean,
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
            if account_row and account_row.get("kind"):
                mail_source = str(account_row.get("kind")).strip().lower()
            elif any(dom in email_clean for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
                mail_source = "outlook"
            elif any(dom in email_clean for dom in ("@icloud.", "@me.", "@mac.")):
                def_source = (db.get_setting("mail_source", "") or "").strip().lower()
                mail_source = "remail" if def_source == "remail" else "icloud_relay"
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
            if mail_source in ("outlook", "remail"):
                raise RuntimeError(f"初始化 {mail_source} 邮箱 Provider 异常: {e}")
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

    # 2. 发送重置密码邮件 / 准备接收验证码
    _step("向 OpenAI 官方申请发送重置密码邮件...")
    _log("正在向 auth.openai.com/api/accounts/password/send-otp 发起重置发码请求...")
    t_sent = time.time()
    ref = continue_url if (continue_url and "auth.openai.com" in continue_url) else "https://auth.openai.com/log-in/password"
    try:
        flow.send_password_reset_otp(referer=ref)
    except Exception as e:
        _log(f"重置发码提示: {e}，继续监听邮箱接收验证码...")

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

    # 5. 向 OpenAI 官方服务端提交新登录密码（无论是改密还是免密账号补设密码）
    _step("正在向 OpenAI 官方服务端提交新密码...")
    _log(f"正在向官方提交新登录密码...")
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
