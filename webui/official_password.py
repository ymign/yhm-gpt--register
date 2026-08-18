"""official_password.py — OpenAI 官方全自动设置 / 重置密码自动化协议
=====================================================================
通过模拟官方 Password Reset 认证链路，实现无需用户手动干预的
端到端自动化给 GPT 账号在 OpenAI 官方服务端真正设置 / 补设 / 修改登录密码。

核心自动化步骤：
1. 向 auth.openai.com 发送密码重置申请：
   POST https://auth.openai.com/api/accounts/user/reset-password/request {"username": email}
2. 自动化邮件提供商（CF 临时邮箱 / Outlook / iCloud 等）收取官方重置邮件（noreply@tm.openai.com）；
3. 提取包含 ticket 认证凭证的重置确认链接；
4. 携带 ticket 提交新密码：
   POST https://auth.openai.com/api/accounts/user/reset-password/confirm {"password": new_password, "ticket": ticket}
5. 官方服务端生效后（HTTP 200），即时回写 SQLite 本地数据库 (registered 表)。
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

from mail_providers import create_mail_provider, extract_otp
from . import db
from .two_factor import generate_random_password

logger = logging.getLogger("official_password")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _extract_reset_ticket_or_url(text: str) -> tuple[str, str]:
    """从重置邮件内容中提取 Reset 确认链接和 ticket。"""
    if not text:
        return "", ""

    # 1. 匹配标准 confirm / enter-password 链接
    url_patterns = [
        r'https://auth\.openai\.com/u/reset-password/[a-zA-Z0-9_-]+\?[^\s"\'<>]+',
        r'https://auth\.openai\.com/u/reset-password\?[^\s"\'<>]+',
        r'https://auth\.openai\.com/api/accounts/user/reset-password/[^\s"\'<>]+',
    ]

    target_url = ""
    for pat in url_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            target_url = m.group(0).strip().rstrip('.,;)"\'')
            break

    # 2. 提取 ticket
    ticket = ""
    ticket_patterns = [
        r'ticket=([a-zA-Z0-9_\-\.]+)',
        r'token=([a-zA-Z0-9_\-\.]+)',
        r'"ticket"\s*:\s*"([a-zA-Z0-9_\-\.]+)"',
    ]
    for pat in ticket_patterns:
        tm = re.search(pat, target_url or text, re.IGNORECASE)
        if tm:
            ticket = tm.group(1).strip()
            break

    return target_url, ticket


def official_set_account_password(
    email: str,
    new_password: str = "",
    proxy: str = "",
    timeout: int = 50,
) -> dict:
    """全自动向 OpenAI 官方申请重置邮件并设置新密码。

    Args:
        email: 目标账号邮箱
        new_password: 目标密码，为空时自动生成 16 位强随机密码
        proxy: 网络代理
        timeout: 收信等待超时时间（秒）

    Returns:
        dict: {"ok": True, "email": email, "password": new_password, "official_applied": True, "message": "..."}
    """
    from curl_cffi.requests import Session as CffiSession

    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise ValueError("邮箱不能为空")

    if not new_password:
        new_password = generate_random_password(16)

    # 1. 获取账号信息与匹配的 Mail Provider
    row = db.get_registered(email_clean) or {}
    account_row = db.get_account_by_email(email_clean) or {}
    mail_source = (row.get("kind") or account_row.get("kind") or db.get_setting("mail_source", "cf_temp")).strip().lower()

    settings = db.get_mail_settings()
    try:
        mail_provider = create_mail_provider(mail_source, settings, account_row)
    except Exception:
        mail_provider = create_mail_provider("cf_temp", settings)

    logger.info(f"[official-pwd] 正在为 {email_clean} 启动官方全自动设置密码流程 (Provider: {mail_provider.kind})...")

    # 2. 初始化网络会话
    session = CffiSession(impersonate="chrome136")
    session.trust_env = False
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://auth.openai.com",
        "Referer": "https://auth.openai.com/u/reset-password/request",
        "User-Agent": USER_AGENT,
        "Sec-Ch-Ua": '"Not?A_Brand";v="99", "Chromium";v="136", "Google Chrome";v="136"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    chain_started_at = time.time()

    # 3. 发送官方密码重置申请请求
    try:
        # 先预热访问一下 request 页面
        session.get(
            "https://auth.openai.com/u/reset-password/request",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": USER_AGENT,
            },
            timeout=25,
        )
    except Exception as e:
        logger.debug(f"[official-pwd] 预热访问 reset-password 页面提示: {e}")

    req_url = "https://auth.openai.com/api/accounts/user/reset-password/request"
    resp = session.post(req_url, headers=headers, json={"username": email_clean}, timeout=30)
    if resp.status_code not in (200, 201, 204):
        err_text = (resp.text or "")[:200]
        # 如果返回 404 或无此用户
        if resp.status_code == 404 or "user_not_found" in err_text:
            raise RuntimeError(f"OpenAI 官方未找到该用户账号 ({resp.status_code})")
        logger.warning(f"[official-pwd] reset-password/request 返回 {resp.status_code}: {err_text}")
        if resp.status_code >= 400 and resp.status_code != 429:
            raise RuntimeError(f"向 OpenAI 官方申请重置密码失败 (HTTP {resp.status_code}): {err_text}")

    logger.info(f"[official-pwd] 官方重置邮件发送申请已提交，正在等待收件 (超时: {timeout}s)...")

    # 4. 从邮箱 Provider 轮询抓取重置邮件
    start_wait = time.time()
    reset_url = ""
    reset_ticket = ""

    while time.time() - start_wait < timeout:
        time.sleep(3)
        try:
            raw_mails = []
            if hasattr(mail_provider, "_get_mails"):
                raw_mails = mail_provider._get_mails(email_clean)
            elif hasattr(mail_provider, "_load"):
                raw_mails = mail_provider._load()

            for m in raw_mails:
                content = str(m.get("raw") or m.get("content") or m.get("text") or m.get("html") or m.get("body") or "")
                subject = str(m.get("subject") or "")
                sender = str(m.get("from") or m.get("sender") or m.get("source") or "")

                # 识别是否为重置密码邮件
                if "reset" in content.lower() or "password" in content.lower() or "重置" in subject or "password" in subject.lower() or "ticket" in content:
                    url_found, ticket_found = _extract_reset_ticket_or_url(content)
                    if url_found or ticket_found:
                        reset_url = url_found
                        reset_ticket = ticket_found
                        break

            if reset_url or reset_ticket:
                break
        except Exception as e:
            logger.debug(f"[official-pwd] 轮询邮件异常: {e}")

    if not reset_url and not reset_ticket:
        raise RuntimeError(f"等待接收 OpenAI 官方重置密码邮件超时 ({timeout}s)，未检索到包含重置链接的邮件")

    logger.info(f"[official-pwd] 成功抓取到官方重置凭证: ticket={reset_ticket[:12]}... url={reset_url[:60]}...")

    # 5. 向官方提交新密码完成确认
    confirm_url = "https://auth.openai.com/api/accounts/user/reset-password/confirm"
    confirm_payload: dict[str, Any] = {"password": new_password}
    if reset_ticket:
        confirm_payload["ticket"] = reset_ticket

    # 如果有完整的 reset_url，先访问该 URL 获取上下文
    if reset_url:
        try:
            session.get(
                reset_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "User-Agent": USER_AGENT,
                    "Referer": "https://auth.openai.com/",
                },
                timeout=25,
            )
        except Exception as e:
            logger.debug(f"[official-pwd] 访问 confirm 页面提示: {e}")

    confirm_headers = dict(headers)
    confirm_headers["Referer"] = reset_url or "https://auth.openai.com/u/reset-password/confirm"

    resp_confirm = session.post(confirm_url, headers=confirm_headers, json=confirm_payload, timeout=30)
    if resp_confirm.status_code not in (200, 201, 204):
        err_msg = (resp_confirm.text or "")[:200]
        # 兼容部分场景下表单直接提交到 reset_url 路径
        if reset_url and "confirm" in reset_url:
            resp_confirm2 = session.post(reset_url, headers=confirm_headers, json=confirm_payload, timeout=30)
            if resp_confirm2.status_code not in (200, 201, 204):
                raise RuntimeError(f"提交新密码到 OpenAI 官方失败 (HTTP {resp_confirm.status_code}): {err_msg}")
        else:
            raise RuntimeError(f"提交新密码到 OpenAI 官方失败 (HTTP {resp_confirm.status_code}): {err_msg}")

    logger.info(f"[official-pwd] ✅ 账号 {email_clean} 密码已在 OpenAI 官方服务端成功生效！")

    # 6. 回写本地数据库
    db.update_registered_manual(email_clean, password=new_password)

    return {
        "ok": True,
        "email": email_clean,
        "password": new_password,
        "official_applied": True,
        "message": "密码已在 OpenAI 官方服务端成功生效并持久化到本地数据库",
    }
