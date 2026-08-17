"""本地原生全渠道 ChatGPT Plus 0 元提炼与资格检测引擎。

支持所有渠道本地协议直连提炼（无需外部中转 API 与 CDK）：
  1. 资格检测类:
     - gcash_check: 批量 GCash / 菲律宾短链资格检测
     - oaics_check: 批量 OAICS / 0元优惠券资格检测
     - plus_check:  批量 Plus 试用状态综合检测
  2. 提链 / 出码类:
     - gcash:  批量 GCash 提链 (PH 菲律宾短链接)
     - pix:    批量 PIX 出码 (BR 巴西二维码 / 授权跳转)
     - paypal: 批量 PayPal 提链 (解析出真正的 paypal.com/agreements/approve?ba_token=BA-XXX 授权链接)
     - ideal:  批量 iDEAL 提链 (NL 荷兰银行跳转 / 扫码)
     - upi:    批量 UPI 提链 (IN 印度扫码指令)
     - kakao:  批量 Kakao 提链 (KR 韩国 KakaoPay)
     - momo:   批量 MoMo 提链 (VN 越南 MoMo 钱包)
     - twint:  批量 TWINT 提链 (CH 瑞士扫码)
     - blik:   批量 BLIK 提链 (PL 波兰 BLIK 6位码/跳转)
     - hosted: 批量 Hosted / Stripe 提链 (标准 0 元 Stripe Checkout 托管支付页)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:
    CurlSession = None

try:
    from . import db
    from .proxy_util import (
        normalize_proxy_url,
        new_proxy_session_id,
        route_proxy_country,
        resolve_target_country,
    )
except ImportError:
    import db
    from proxy_util import (
        normalize_proxy_url,
        new_proxy_session_id,
        route_proxy_country,
        resolve_target_country,
    )

# 引入项目内置的高性能 pay_engine 模块 (集成自 pay153 纯协议引擎)
try:
    from .pay_engine import stripe_checkout as sc
    from .pay_engine import provider_checkout as pc
    from .pay_engine.provider_checkout import default_billing
    HAVE_PAY153 = True
except ImportError:
    try:
        from pay_engine import stripe_checkout as sc
        from pay_engine import provider_checkout as pc
        from pay_engine.provider_checkout import default_billing
        HAVE_PAY153 = True
    except Exception:
        HAVE_PAY153 = False
        sc = None
        pc = None
        default_billing = None

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "gcash_check": {"name": "GCash 资格检测", "category": "check", "default_exit": "US", "default_billing": "PH", "default_currency": "PHP"},
    "oaics_check": {"name": "OAICS 资格检测", "category": "check", "default_exit": "DE", "default_billing": "DE", "default_currency": "EUR"},
    "plus_check":  {"name": "Plus 状态检测", "category": "check", "default_exit": "US", "default_billing": "US", "default_currency": "USD"},
    "gcash":       {"name": "GCash 提链", "category": "extract", "default_exit": "US", "default_billing": "PH", "default_currency": "PHP"},
    "pix":         {"name": "PIX 出码", "category": "extract", "default_exit": "BR", "default_billing": "BR", "default_currency": "BRL"},
    "paypal":      {"name": "PayPal 提链", "category": "extract", "default_exit": "DE", "default_billing": "DE", "default_currency": "EUR"},
    "ideal":       {"name": "iDEAL 提链", "category": "extract", "default_exit": "NL", "default_billing": "NL", "default_currency": "EUR"},
    "upi":         {"name": "UPI 提链", "category": "extract", "default_exit": "IN", "default_billing": "IN", "default_currency": "INR"},
    "kakao":       {"name": "Kakao 提链", "category": "extract", "default_exit": "KR", "default_billing": "KR", "default_currency": "KRW"},
    "momo":        {"name": "MoMo 提链", "category": "extract", "default_exit": "VN", "default_billing": "VN", "default_currency": "VND"},
    "twint":       {"name": "TWINT 提链", "category": "extract", "default_exit": "CH", "default_billing": "CH", "default_currency": "CHF"},
    "blik":        {"name": "BLIK 提链", "category": "extract", "default_exit": "PL", "default_billing": "PL", "default_currency": "PLN"},
    "hosted":      {"name": "Hosted 提链", "category": "extract", "default_exit": "US", "default_billing": "US", "default_currency": "USD"},
}

STRIPE_API = "https://api.stripe.com"
KNOWN_PKS = {
    "openai_ie": "pk_live_51Pj377KslHRdbaPgTJYjThzH3f5dt1N1vK7LUp0qh0yNSarhfZ6nfbG7FFlh8KLxVkvdMWN5o6Mc4Vda6NHaSnaV00C2Sbl8Zs",
    "openai_llc": "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n",
}
STRIPE_VERSION_FULL = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
STRIPE_VERSION_BASE = "2025-03-31.basil"
DEFAULT_RUNTIME_VERSION = "6f8494a281"

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(DEFAULT_USER_AGENTS)


def _normalize_proxy_url(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy or proxy.startswith("#"):
        return ""
    if "://" in proxy:
        return proxy
    if "@" in proxy:
        return f"http://{proxy}"
    raw_parts = proxy.rsplit(":", 2)
    if len(raw_parts) == 3 and raw_parts[2].isdigit() and ":" in raw_parts[0]:
        credentials, host, port = raw_parts
        username, password = credentials.split(":", 1)
        if username and password and host:
            return f"http://{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}:{port}"
    parts = proxy.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in proxy:
        host, port, username, password = parts
        return f"http://{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}:{port}"
    return f"http://{proxy}"


def _get_proxy_url(pool_str: str = "", country: str = "") -> Optional[str]:
    lines = [line.strip() for line in (pool_str or "").splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return None
    p = random.choice(lines)
    sid = new_proxy_session_id()
    if country:
        target_c = resolve_target_country(country) or country.upper()
        p = route_proxy_country(p, target_c, session_id=sid)
        if "{country}" in p or "[country]" in p:
            p = p.replace("{country}", target_c.lower()).replace("[country]", target_c.lower())
    else:
        p = route_proxy_country(p, session_id=sid)
    return normalize_proxy_url(p)


def _create_http_client(proxy: Optional[str] = None):
    ua = _random_ua()
    if CurlSession is not None:
        kwargs: dict[str, Any] = {"impersonate": "firefox135"}
        if proxy:
            kwargs["proxy"] = _normalize_proxy_url(proxy)
        s = CurlSession(**kwargs)
        s.headers["User-Agent"] = ua
        return s
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = ua
    if proxy:
        p_norm = _normalize_proxy_url(proxy)
        s.proxies = {"http": p_norm, "https": p_norm}
    return s


def _extract_redirect_url(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    m = re.search(r"https?://(?:www\.)?paypal\.com/(?:agreements/approve|checkoutnow)\?[^\s\"'<>\\]+", raw)
    if m:
        return m.group(0).replace("\\u0026", "&").replace("\\/", "/")
    m = re.search(r"https?://pm-redirects\.stripe\.com/authorize/[^\s\"'<>\\]+", raw)
    if m:
        return m.group(0).replace("\\u0026", "&").replace("\\/", "/")
    return ""


def _resolve_paypal_agreements_url(http_client, redirect_url: str) -> str:
    current = str(redirect_url or "")
    if "paypal.com/agreements/approve" in current:
        return current
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for _ in range(5):
        if re.search(r"paypal\.com/agreements/approve\?.*\bba_token=", current, re.I):
            return current
        try:
            resp = http_client.get(current, headers=headers, allow_redirects=False, timeout=20)
        except Exception:
            return redirect_url
        location = str((getattr(resp, "headers", {}) or {}).get("location") or "")
        if location:
            current = urljoin(current, location)
            continue
        text = getattr(resp, "text", "") or ""
        match = re.search(r"https?://(?:www\.)?paypal\.com/agreements/approve\?[^\s\"'<>]+", text, re.I)
        if match:
            return match.group(0).replace("&amp;", "&")
        break
    return current or redirect_url


def _extract_oaics_flow(
    client,
    proxy: str,
    email: str,
    checkout_data: dict,
    channel: str,
    billing_country: str,
    currency: str,
    log_fn: Callable[[str], None],
) -> str:
    """完整实现 OAICS 提炼协议 (Elements -> Taxes -> CToken -> OpenAI Confirm -> Intent Confirm -> BA Token)."""
    checkout_session_id = str(checkout_data.get("checkout_session_id") or checkout_data.get("id") or "")
    customer_secret = str(checkout_data.get("customer_session_client_secret") or "").strip()
    processor_entity = str(checkout_data.get("processor_entity") or ("openai_llc" if billing_country == "US" else "openai_ie"))
    pk = KNOWN_PKS.get(processor_entity, KNOWN_PKS["openai_ie"])

    stripe_client = _create_http_client(proxy)
    stripe_hdrs = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
    }

    if billing_country == "DE":
        billing_name = "Alex Schmidt"
        line1 = "Friedrichstrasse 1"
        city = "Berlin"
        postal_code = "10115"
        state = "Berlin"
        phone = "+491512345678"
    elif billing_country == "BR":
        billing_name = "Lucas Silva"
        line1 = "Av. Paulista 1000"
        city = "Sao Paulo"
        postal_code = "01310-100"
        state = "SP"
        phone = "+5511987654321"
    else:
        billing_name = "John Doe"
        line1 = "123 Main St"
        city = "New York"
        postal_code = "10001"
        state = "NY"
        phone = "+12025550123"

    guid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
    muid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
    sid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
    stripe_js_id = str(uuid.uuid4())
    runtime = DEFAULT_RUNTIME_VERSION

    # 1. Elements Session 初始化
    log_fn("[oaics] 正在初始化 Stripe Elements 会话...")
    customer_id = ""
    elem_session_id = ""
    if customer_secret:
        elem_params = {
            "customer_session_client_secret": customer_secret,
            "key": pk,
            "_stripe_version": STRIPE_VERSION_BASE,
            "elements_init_source": "stripe.elements",
            "referrer_host": "chatgpt.com",
            "stripe_js_id": stripe_js_id,
            "locale": "de-DE" if billing_country == "DE" else ("pt-BR" if billing_country == "BR" else "en-US"),
            "type": "deferred_intent",
            "deferred_intent[mode]": "subscription",
            "deferred_intent[amount]": "0",
            "deferred_intent[currency]": currency.lower(),
            "deferred_intent[setup_future_usage]": "off_session",
            "currency": currency.lower(),
        }
        methods = checkout_data.get("payment_method_types") or ["card", "paypal", "link"]
        for idx, m in enumerate(methods):
            elem_params[f"deferred_intent[payment_method_types][{idx}]"] = str(m)
        try:
            r_elem = stripe_client.get(
                f"{STRIPE_API}/v1/elements/sessions",
                params=elem_params,
                headers=stripe_hdrs,
                timeout=25,
            )
            if r_elem.status_code == 200:
                elem_data = r_elem.json() or {}
                elem_session_id = str(elem_data.get("session_id") or "")
                cust = elem_data.get("customer") or {}
                if isinstance(cust, dict):
                    csess = cust.get("customer_session") or {}
                    customer_id = str(csess.get("customer") or cust.get("id") or "")
                log_fn(f"[oaics] Elements Session 初始化成功 (session_id={elem_session_id[:16]}...)")
        except Exception as e:
            log_fn(f"[oaics] Elements Session 提示: {e}")

    # 2. 提交 Taxes 校验
    log_fn("[oaics] 正在提交 Taxes 账单数据...")
    tax_body = {
        "checkout_session_id": checkout_session_id,
        "checkout_email": email,
        "billing_country": billing_country,
        "billing_name": billing_name,
        "currency": currency.upper(),
        "processor_entity": processor_entity,
        "billing_address": {
            "country": billing_country,
            "line1": line1,
            "city": city,
            "postal_code": postal_code,
            "state": state,
        },
    }
    try:
        r_tax = client.post(
            "https://chatgpt.com/backend-api/payments/checkout/taxes",
            json=tax_body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://chatgpt.com",
                "Referer": f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
                "x-openai-target-path": "/backend-api/payments/checkout/taxes",
                "x-openai-target-route": "/backend-api/payments/checkout/taxes",
            },
            timeout=25,
        )
        log_fn(f"[oaics] Taxes 响应 HTTP {r_tax.status_code}")
    except Exception as e:
        log_fn(f"[oaics] Taxes 提示: {e}")

    # 3. 生成 Stripe Confirmation Token (ctoken_)
    log_fn("[oaics] 正在生成 Stripe 确认令牌 (Confirmation Token)...")
    pm_type = "paypal" if channel == "paypal" else channel
    ctoken_body = {
        "payment_method_data[type]": pm_type,
        "payment_method_data[billing_details][name]": billing_name,
        "payment_method_data[billing_details][email]": email,
        "payment_method_data[billing_details][phone]": phone,
        "payment_method_data[billing_details][address][country]": billing_country,
        "payment_method_data[billing_details][address][city]": city,
        "payment_method_data[billing_details][address][postal_code]": postal_code,
        "payment_method_data[billing_details][address][line1]": line1,
        "payment_method_data[billing_details][address][state]": state,
        "payment_method_data[payment_user_agent]": f"stripe.js/{runtime}; stripe-js-v3/{runtime}; payment-element; deferred-intent",
        "payment_method_data[referrer]": "https://chatgpt.com",
        "payment_method_data[time_on_page]": str(random.randint(50000, 80000)),
        "payment_method_data[guid]": guid,
        "payment_method_data[muid]": muid,
        "payment_method_data[sid]": sid,
        "setup_future_usage": "off_session",
        "mandate_data[customer_acceptance][type]": "online",
        "mandate_data[customer_acceptance][online][infer_from_client]": "true",
        "client_context[currency]": currency.lower(),
        "client_context[mode]": "subscription",
        "set_as_default_payment_method": "false",
        "key": pk,
        "_stripe_version": STRIPE_VERSION_BASE,
        "payment_method_data[client_attribution_metadata][client_session_id]": stripe_js_id,
        "payment_method_data[client_attribution_metadata][merchant_integration_source]": "l1",
        "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][0]": "expressCheckout",
        "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][1]": "payment",
        "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][2]": "address",
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "l1",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "expressCheckout",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][2]": "address",
    }
    if customer_id:
        ctoken_body["client_context[customer]"] = customer_id

    r_ct = stripe_client.post(
        f"{STRIPE_API}/v1/confirmation_tokens",
        data=ctoken_body,
        headers=stripe_hdrs,
        timeout=25,
    )
    if r_ct.status_code != 200:
        err_msg = r_ct.text[:200]
        log_fn(f"[oaics] Confirmation Token 异常: {err_msg}")
        raise RuntimeError(f"Stripe confirmation token 失败 (HTTP {r_ct.status_code}): {err_msg}")

    ct_data = r_ct.json() or {}
    ctoken_id = str(ct_data.get("id") or "")
    if not ctoken_id.startswith("ctoken_"):
        raise RuntimeError("Stripe confirmation token 响应缺失 ctoken_")
    log_fn(f"[oaics] 成功获取确认令牌: {ctoken_id[:20]}...")

    # 4. 提交 OpenAI Checkout Confirm
    log_fn("[oaics] 正在向 OpenAI 提交 Checkout Confirm...")
    confirm_body = {
        "checkout_session_id": checkout_session_id,
        "confirm_token": ctoken_id,
        "selected_payment_method_type": pm_type,
    }
    r_conf = client.post(
        "https://chatgpt.com/backend-api/payments/checkout/confirm",
        json=confirm_body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
            "x-openai-target-path": "/backend-api/payments/checkout/confirm",
            "x-openai-target-route": "/backend-api/payments/checkout/confirm",
        },
        timeout=25,
    )
    if r_conf.status_code not in (200, 201):
        err_msg = r_conf.text[:200]
        log_fn(f"[oaics] Checkout Confirm 异常: {err_msg}")
        raise RuntimeError(f"OpenAI checkout/confirm 失败 (HTTP {r_conf.status_code}): {err_msg}")

    conf_data = r_conf.json() or {}
    client_secret = str(conf_data.get("client_secret") or "").strip()
    if not client_secret or "_secret_" not in client_secret:
        raise RuntimeError("OpenAI confirm 响应未返回有效的 client_secret")
    log_fn("[oaics] OpenAI Confirm 成功，已获取 client_secret")

    # 5. 向 Stripe 发起 Intent Confirm
    log_fn("[oaics] 正在向 Stripe 确认支付意图 (Intent Confirm)...")
    intent_id = client_secret.split("_secret_", 1)[0]
    if intent_id.startswith("seti_"):
        intent_endpoint = f"{STRIPE_API}/v1/setup_intents/{intent_id}/confirm"
    else:
        intent_endpoint = f"{STRIPE_API}/v1/payment_intents/{intent_id}/confirm"

    return_url = f"https://pay.openai.com/c/pay/{checkout_session_id}?redirect_pm_type={pm_type}&ui_mode=custom"
    intent_body = {
        "return_url": return_url,
        "confirmation_token": ctoken_id,
        "key": pk,
        "_stripe_version": STRIPE_VERSION_FULL,
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "l1",
        "client_secret": client_secret,
    }
    r_intent = stripe_client.post(intent_endpoint, data=intent_body, headers=stripe_hdrs, timeout=25)
    if r_intent.status_code not in (200, 201):
        err_msg = r_intent.text[:200]
        log_fn(f"[oaics] Intent Confirm 异常: {err_msg}")
        raise RuntimeError(f"Stripe Intent Confirm 失败 (HTTP {r_intent.status_code}): {err_msg}")

    intent_data = r_intent.json() or {}
    next_action = intent_data.get("next_action") or {}
    stripe_redirect = str(next_action.get("redirect_to_url", {}).get("url") or intent_data.get("redirect_to_url") or "").strip()
    if not stripe_redirect:
        raise RuntimeError("Stripe Intent Confirm 未返回重定向 URL (redirect_to_url)")

    log_fn(f"[oaics] 获得 Stripe 中转地址: {stripe_redirect[:50]}...")

    # 6. 跟踪重定向获取最终 Provider URL (如 PayPal BA Token)
    if channel == "paypal":
        log_fn("[oaics] 正在追踪解析 PayPal 最终 BA 授权链接...")
        provider_url = _resolve_paypal_agreements_url(stripe_client, stripe_redirect)
        if "ba_token=" not in provider_url:
            provider_url = _resolve_paypal_agreements_url(client, stripe_redirect)
        return provider_url
    return stripe_redirect


class ExtractJobTask:
    """多账号并发提炼任务。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.channel = config.get("channel", "paypal")
        self.channel_meta = CHANNEL_META.get(self.channel, CHANNEL_META["paypal"])
        self.exit_country = (config.get("exit_country") or self.channel_meta["default_exit"]).upper()
        self.billing_country = (config.get("billing_country") or self.channel_meta["default_billing"]).upper()
        self.currency = (config.get("currency") or self.channel_meta["default_currency"]).upper()
        self.workers = max(1, min(20, int(config.get("workers") or 3)))
        self.retries = max(1, min(10, int(config.get("retries") or 3)))
        self.allow_fallback = bool(config.get("allow_fallback", True))
        self.proxy_pool = config.get("proxy_pool", "")
        self.started_at = time.time()
        self.finished_at = 0.0
        self.cancelled = False

        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
                "step_text": "待启动",
                "channel": self.channel,
                "link_url": "",
                "result": None,
                "started_at": 0.0,
                "finished_at": 0.0,
                "elapsed": 0.0,
                "logs": [],
            }
            for e in emails
        }
        self.queue: queue.Queue = queue.Queue()
        self.done_count = 0
        self.stats = {"success": 0, "error": 0, "stopped": 0}
        self._lock = threading.Lock()

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

    def set_running(self, email: str, step_text: str = "正在发起提炼...") -> None:
        now = time.time()
        with self._lock:
            if email in self.items:
                self.items[email]["status"] = "running"
                self.items[email]["step_text"] = step_text
                if not self.items[email]["started_at"]:
                    self.items[email]["started_at"] = now
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step_text": step_text,
            "started_at": now,
        })

    def mark_done(self, email: str, result: dict) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            st = result.get("status") or "error"
            if st == "success":
                self.stats["success"] += 1
            elif st == "cancelled":
                self.stats["stopped"] += 1
            else:
                self.stats["error"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = st
                it["result"] = result
                it["link_url"] = result.get("link_url") or ""
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)
                it["step_text"] = result.get("label") or ("提链成功" if st == "success" else "提炼完成")

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": st,
            "result": result,
            "link_url": result.get("link_url") or "",
            "step_text": result.get("label") or ("提链成功" if st == "success" else "提炼完成"),
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_active_tasks: dict[str, ExtractJobTask] = {}
_tasks_lock = threading.Lock()


def _execute_account_extract(task: ExtractJobTask, email: str) -> None:
    """单个账号执行提炼流程。"""
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已停止", "error": "任务已手动停止"})
        return

    task.set_running(email, "正在获取账号凭证...")
    task.add_email_log(email, f"开始【{task.channel_meta['name']}】: 出口={task.exit_country} | 账单={task.billing_country} | 币种={task.currency}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "error", "label": "账号不存在", "error": "数据库中无此账号"}
        task.add_email_log(email, "错误: 数据库中找不到此已注册账号")
        task.mark_done(email, res)
        return

    access_token = str(cred.get("access_token") or "").strip()
    session_token = str(cred.get("session_token") or "").strip()

    if not access_token and not session_token:
        res = {"status": "error", "label": "缺少Token", "error": "账号缺少 access_token / session_token"}
        task.add_email_log(email, "错误: 账号缺少 Token，无法向 OpenAI 请求 Checkout")
        task.mark_done(email, res)
        return

    start_ts = time.time()
    proxy = _get_proxy_url(task.proxy_pool, task.exit_country)

    for attempt in range(1, task.retries + 1):
        if task.cancelled:
            task.mark_done(email, {"status": "cancelled", "label": "已停止", "error": "任务已中止"})
            return

        if attempt > 1:
            # 账号级防频控退避 + 强制轮换新代理 Session
            proxy = _get_proxy_url(task.proxy_pool, task.exit_country)
            sleep_sec = 2.5 + random.uniform(0.5, 1.5)
            time.sleep(sleep_sec)

        try:
            task.set_running(email, f"发起支付 Checkout ({attempt}/{task.retries})...")
            task.add_email_log(email, f"第 {attempt} 次尝试: 请求 OpenAI Checkout 接口...")

            client = _create_http_client(proxy)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "x-openai-target-path": "/backend-api/payments/checkout",
                "x-openai-target-route": "/backend-api/payments/checkout",
            }
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            if session_token:
                headers["Cookie"] = f"__Secure-next-auth.session-token={session_token}"

            # 1. 创建 Checkout (直接在创建时携带 promo_campaign 0元试用)
            create_url = "https://chatgpt.com/backend-api/payments/checkout"
            create_body = {
                "entry_point": "all_plans_pricing_modal",
                "plan_name": "chatgptplusplan",
                "billing_details": {
                    "country": task.billing_country,
                    "currency": task.currency.upper(),
                },
                "checkout_ui_mode": "custom",
                "promo_campaign": {
                    "promo_campaign_id": "plus-1-month-free",
                    "is_coupon_from_query_param": False,
                },
            }
            resp = client.post(create_url, json=create_body, headers=headers, timeout=25)
            task.add_email_log(email, f"Checkout 响应 HTTP {resp.status_code}")

            if resp.status_code not in (200, 201):
                err_text = (resp.text or "")[:300]
                if resp.status_code == 429:
                    task.add_email_log(email, f"触发 OpenAI 账号频控 (429 Too Many Requests)，正在轮换代理并退避冷却 ({attempt}/{task.retries})...")
                    time.sleep(4.0 * attempt + random.uniform(2.0, 4.0))
                    proxy = _get_proxy_url(task.proxy_pool, task.exit_country)
                    continue
                if "account_deactivated" in err_text:
                    raise RuntimeError("账号已被封禁 (account_deactivated)")
                if "token_expired" in err_text or resp.status_code == 401:
                    raise RuntimeError("凭证已失效 (401)")
                raise RuntimeError(f"OpenAI Checkout 失败 HTTP {resp.status_code}: {err_text}")

            data = resp.json() or {}
            checkout_session_id = str(data.get("checkout_session_id") or data.get("id") or data.get("session_id") or "")
            checkout_url = str(data.get("checkout_url") or data.get("url") or "")
            processor_entity = str(data.get("processor_entity") or ("openai_llc" if task.billing_country == "US" else "openai_ie"))
            pk = KNOWN_PKS.get(processor_entity, KNOWN_PKS["openai_ie"])

            # 2. 资格检测分支
            if "check" in task.channel:
                if task.channel == "oaics_check":
                    if checkout_session_id.startswith("oaics_"):
                        oaics_state = "OAICS"
                        label = "🎯 命中OAICS"
                        status = "success"
                    elif checkout_session_id.startswith("cs_"):
                        oaics_state = "CS"
                        label = "CS (普通Stripe)"
                        status = "cs"
                    elif checkout_session_id.startswith("oaic_"):
                        oaics_state = "OAIC"
                        label = "OAIC (内部会话)"
                        status = "oaic"
                    else:
                        oaics_state = "NONE"
                        label = "未命中"
                        status = "error"
                elif task.channel == "gcash_check":
                    is_gcash = "gcash" in (resp.text or "").lower() or checkout_session_id.startswith("cs_")
                    oaics_state = "GCASH" if is_gcash else "NON_GCASH"
                    label = "✅ 命中GCash" if is_gcash else "未命中"
                    status = "success" if is_gcash else "error"
                else:
                    promo_info = data.get("promo_campaign") or {}
                    has_promo_id = bool(str(promo_info.get("promo_campaign_id") or "").strip())
                    total_info = data.get("checkout_state", {}).get("total", {})
                    is_zero_due = total_info.get("total", {}).get("minorUnitsAmount") == 0 or total_info.get("discount", {}).get("minorUnitsAmount", 0) > 0
                    is_eligible = has_promo_id or is_zero_due
                    oaics_state = "PLUS_ELIGIBLE" if is_eligible else ("CS" if checkout_session_id.startswith("cs_") else "FREE")
                    label = "Plus试用" if is_eligible else "普通/已开通"
                    status = "success" if is_eligible else "free"

                res = {
                    "status": status,
                    "label": label,
                    "state": oaics_state,
                    "checkout_url": checkout_url or f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
                    "link_url": checkout_url or f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
                    "req_ms": int((time.time() - start_ts) * 1000),
                }
                task.add_email_log(email, f"🎉 检测完成: {label} (Session: {checkout_session_id[:16]}...)")
                db.update_oa_check(email, {"state": oaics_state, "checked_at": time.time(), "url": res["link_url"]})
                task.mark_done(email, res)
                return

            final_link = ""

            # 3. 处理 OAICS 自建会话 (无论何种渠道，统一走标准 OAICS 协议栈)
            if checkout_session_id.startswith("oaics_"):
                if task.channel == "hosted":
                    final_link = checkout_url or f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}"
                else:
                    task.set_running(email, f"正在执行 OAICS 深度协议解析 ({task.channel.upper()})...")
                    task.add_email_log(email, f"OAICS 会话：启动 Stripe 确认令牌与意图确认协议 ({task.channel.upper()})...")
                    try:
                        final_link = _extract_oaics_flow(
                            client=client,
                            proxy=proxy,
                            email=email,
                            checkout_data=data,
                            channel=task.channel,
                            billing_country=task.billing_country,
                            currency=task.currency,
                            log_fn=lambda m: task.add_email_log(email, m),
                        )
                        if final_link:
                            task.add_email_log(email, f"🎉 OAICS 成功解析出 {task.channel.upper()} 跳转: {final_link}")
                    except Exception as exc:
                        task.add_email_log(email, f"OAICS {task.channel.upper()} 协议解析异常: {exc}")
                        raise RuntimeError(f"OAICS {task.channel.upper()} 提炼失败: {exc}") from exc

            # 4. 如果是标准的 cs_live 会话
            elif checkout_session_id.startswith("cs_"):
                if task.channel == "hosted":
                    final_link = checkout_url or f"https://pay.openai.com/c/pay/{checkout_session_id}"
                elif task.channel == "paypal" and HAVE_PAY153 and sc is not None:
                    task.set_running(email, "正在执行 Stripe/PayPal 协议并解析 ba_token...")
                    task.add_email_log(email, "执行 PayPal 深度协议解析 (init -> tax -> 0元校验 -> confirm -> approve -> ba_token)...")
                    try:
                        billing = default_billing(task.billing_country, email=email) if default_billing else {"email": email, "name": "Alex Schmidt", "address": {"country": task.billing_country, "city": "Berlin", "postal_code": "10115", "line1": "Friedrichstrasse 1"}}
                        def _apply_promo_cb(entity):
                            try:
                                client.post(
                                    "https://chatgpt.com/backend-api/payments/checkout/update",
                                    json={
                                        "checkout_session_id": checkout_session_id,
                                        "processor_entity": entity or processor_entity,
                                        "plan_name": "chatgptplusplan",
                                        "price_interval": "month",
                                        "seat_quantity": 1,
                                        "promo_campaign": {
                                            "promo_campaign_id": "plus-1-month-free",
                                            "is_coupon_from_query_param": False,
                                        },
                                    },
                                    headers={
                                        **headers,
                                        "Referer": f"https://chatgpt.com/checkout/{entity or processor_entity}/{checkout_session_id}",
                                        "x-openai-target-path": "/backend-api/payments/checkout/update",
                                        "x-openai-target-route": "/backend-api/payments/checkout/update",
                                    },
                                    timeout=20,
                                )
                            except Exception:
                                pass

                        paypal_url, _, _ = sc.stripe_to_paypal_redirect(
                            client,
                            checkout_session_id,
                            billing=billing,
                            country=task.billing_country,
                            publishable_key=pk,
                            processor_entity=processor_entity,
                            chatgpt_http=client,
                            access_token=access_token,
                            apply_promo_callback=_apply_promo_cb,
                            require_zero_due=True,
                            log=lambda m: task.add_email_log(email, m),
                        )
                        if paypal_url:
                            final_link = paypal_url
                            task.add_email_log(email, f"🎉 成功解析出官方 0元 PayPal 授权链接: {paypal_url}")
                    except Exception as e:
                        task.add_email_log(email, f"PayPal 深度协议解析失败: {e}")
                        raise RuntimeError(f"PayPal 0元协议提炼失败: {e}") from e

            # 5. 其它通用渠道处理
            if not final_link:
                if task.channel in ("pix", "ideal", "upi", "twint", "blik") and checkout_session_id.startswith("cs_"):
                    task.set_running(email, "正在初始化渠道会话...")
                    stripe_client = _create_http_client(proxy)
                    stripe_hdrs = {
                        "User-Agent": _random_ua(),
                        "Accept": "application/json",
                        "Origin": "https://pay.openai.com",
                        "Referer": "https://pay.openai.com/",
                        "Content-Type": "application/x-www-form-urlencoded",
                    }

                    stripe_js_id = str(uuid.uuid4())
                    elements_session_id = f"elements_session_{uuid.uuid4().hex[:11]}"
                    init_body = {
                        "browser_locale": "de-DE",
                        "browser_timezone": "Europe/Berlin",
                        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
                        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
                        "elements_session_client[elements_init_source]": "custom_checkout",
                        "elements_session_client[referrer_host]": "chatgpt.com",
                        "elements_session_client[stripe_js_id]": stripe_js_id,
                        "elements_session_client[locale]": "de",
                        "elements_session_client[is_aggregation_expected]": "false",
                        "key": pk,
                        "_stripe_version": STRIPE_VERSION_FULL,
                    }
                    r_init = stripe_client.post(f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/init", data=init_body, headers=stripe_hdrs, timeout=25)
                    init_data = r_init.json() or {} if r_init.status_code == 200 else {}
                    init_checksum = init_data.get("init_checksum") or ""
                    config_id = init_data.get("config_id") or ""

                    # 提交 tax_region
                    tax_body = {
                        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
                        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
                        "elements_session_client[elements_init_source]": "custom_checkout",
                        "elements_session_client[referrer_host]": "chatgpt.com",
                        "elements_session_client[stripe_js_id]": stripe_js_id,
                        "elements_session_client[session_id]": elements_session_id,
                        "elements_session_client[locale]": "de",
                        "elements_session_client[is_aggregation_expected]": "false",
                        "key": pk,
                        "_stripe_version": STRIPE_VERSION_FULL,
                        "tax_region[country]": task.billing_country,
                        "tax_region[city]": "Berlin",
                        "tax_region[postal_code]": "10115",
                        "tax_region[line1]": "Friedrichstrasse 1",
                        "tax_region[state]": "Berlin",
                    }
                    stripe_client.post(f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}", data=tax_body, headers=stripe_hdrs, timeout=25)

                    # Confirm
                    guid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
                    muid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
                    sid = f"{uuid.uuid4()}{secrets.token_hex(3)}"
                    checkout_return_url = f"https://pay.openai.com/c/pay/{checkout_session_id}?redirect_pm_type={task.channel}&ui_mode=hosted"

                    confirm_body = {
                        "guid": guid,
                        "muid": muid,
                        "sid": sid,
                        "expected_amount": "0",
                        "expected_payment_method_type": task.channel if task.channel in ("pix", "ideal", "upi") else "paypal",
                        "key": pk,
                        "_stripe_version": STRIPE_VERSION_FULL,
                        "init_checksum": init_checksum,
                        "version": DEFAULT_RUNTIME_VERSION,
                        "return_url": checkout_return_url,
                        "consent[terms_of_service]": "accepted",
                        "elements_session_client[elements_init_source]": "custom_checkout",
                        "elements_session_client[referrer_host]": "chatgpt.com",
                        "elements_session_client[stripe_js_id]": stripe_js_id,
                        "elements_session_client[locale]": "de",
                        "elements_session_client[is_aggregation_expected]": "false",
                        "elements_session_client[session_id]": elements_session_id,
                        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
                        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
                        "client_attribution_metadata[client_session_id]": stripe_js_id,
                        "client_attribution_metadata[checkout_session_id]": checkout_session_id,
                        "client_attribution_metadata[checkout_config_id]": config_id,
                        "client_attribution_metadata[elements_session_id]": elements_session_id,
                        "client_attribution_metadata[elements_session_config_id]": str(uuid.uuid4()),
                        "client_attribution_metadata[merchant_integration_source]": "checkout",
                        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
                        "client_attribution_metadata[merchant_integration_version]": "custom",
                        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
                        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
                        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
                        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
                        "payment_method_data[type]": task.channel if task.channel in ("pix", "ideal", "upi") else "paypal",
                        "payment_method_data[billing_details][name]": "Alex Schmidt",
                        "payment_method_data[billing_details][email]": email,
                        "payment_method_data[billing_details][address][country]": task.billing_country,
                        "payment_method_data[billing_details][address][line1]": "Friedrichstrasse 1",
                        "payment_method_data[billing_details][address][city]": "Berlin",
                        "payment_method_data[billing_details][address][postal_code]": "10115",
                        "payment_method_data[billing_details][address][state]": "Berlin",
                        "payment_method_data[client_attribution_metadata][client_session_id]": stripe_js_id,
                        "payment_method_data[client_attribution_metadata][checkout_session_id]": checkout_session_id,
                        "payment_method_data[client_attribution_metadata][checkout_config_id]": config_id,
                        "payment_method_data[client_attribution_metadata][elements_session_id]": elements_session_id,
                        "payment_method_data[client_attribution_metadata][elements_session_config_id]": str(uuid.uuid4()),
                        "payment_method_data[client_attribution_metadata][merchant_integration_source]": "elements",
                        "payment_method_data[client_attribution_metadata][merchant_integration_subtype]": "payment-element",
                        "payment_method_data[client_attribution_metadata][merchant_integration_version]": "2021",
                        "payment_method_data[client_attribution_metadata][payment_intent_creation_flow]": "deferred",
                        "payment_method_data[client_attribution_metadata][payment_method_selection_flow]": "automatic",
                        "payment_method_data[payment_user_agent]": f"stripe.js/{DEFAULT_RUNTIME_VERSION}; stripe-js-v3/{DEFAULT_RUNTIME_VERSION}; payment-element; deferred-intent",
                        "payment_method_data[referrer]": "https://chatgpt.com",
                        "payment_method_data[time_on_page]": "32451",
                    }
                    r_conf = stripe_client.post(f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/confirm", data=confirm_body, headers=stripe_hdrs, timeout=30)
                    conf_data = r_conf.json() or {} if r_conf.status_code == 200 else {}
                    final_link = _extract_redirect_url(conf_data)

                    # manual_approval approve
                    try:
                        approve_body = {"checkout_session_id": checkout_session_id, "processor_entity": processor_entity}
                        client.post(
                            "https://chatgpt.com/backend-api/payments/checkout/approve",
                            json=approve_body,
                            headers={
                                "Referer": f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
                                "x-openai-target-path": "/backend-api/payments/checkout/approve",
                                "x-openai-target-route": "/backend-api/payments/checkout/approve",
                            },
                            timeout=20,
                        )
                    except Exception:
                        pass

                    # Poll
                    if not final_link:
                        for _ in range(3):
                            r_poll = stripe_client.get(f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/poll", params={"key": pk, "_stripe_version": STRIPE_VERSION_BASE}, headers=stripe_hdrs, timeout=20)
                            pdata = r_poll.json() or {} if r_poll.status_code == 200 else {}
                            final_link = _extract_redirect_url(pdata)
                            if final_link:
                                break
                            time.sleep(1.0)

                    # 尝试读取完整的带 hash 的 stripe_hosted_url
                    if not final_link:
                        hosted_raw = str(init_data.get("stripe_hosted_url") or "")
                        if hosted_raw and "#fidnandh" in hosted_raw:
                            final_link = hosted_raw

            # 6. 链接校验与零元断言 (非0元/无有效协议必须阻断为失败)
            if task.channel == "paypal":
                if not final_link or "ba_token=" not in final_link:
                    raise RuntimeError("未能生成合法的 0 元 PayPal 授权签约协议 (未获取到 BA-token 或非 0 元)")
            elif not final_link:
                if task.channel == "gcash":
                    final_link = f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}"
                elif task.channel == "upi":
                    final_link = f"https://payments.stripe.com/upi/instructions/{checkout_session_id}"
                else:
                    raise RuntimeError(f"未能生成合法的 0 元 {task.channel.upper()} 支付链接")

            # 解析 ba_token 方便展示
            ba_token = ""
            m_ba = re.search(r"ba_token=([A-Za-z0-9-]+)", final_link)
            if m_ba:
                ba_token = m_ba.group(1)

            req_ms = int((time.time() - start_ts) * 1000)
            task.add_email_log(email, f"🎉 0元提链成功 ({req_ms}ms): {final_link}")

            # 回写数据库 (仅 100% 验证为 0 元的有效链接才回写为 success)
            db.update_registered_extract(
                email=email,
                extract_data={
                    "status": "success",
                    "channel": task.channel,
                    "link_type": task.channel,
                    "link_url": final_link,
                    "cs_id": checkout_session_id,
                    "ba_token": ba_token,
                    "is_zero_trial": True,
                    "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )

            res = {
                "status": "success",
                "label": "0元生效",
                "link_url": final_link,
                "cs_id": checkout_session_id,
                "channel": task.channel,
                "ba_token": ba_token,
                "is_zero_trial": True,
                "req_ms": req_ms,
            }
            task.mark_done(email, res)
            return

        except Exception as e:
            err_msg = str(e)
            task.add_email_log(email, f"第 {attempt} 次失败: {err_msg}")
            if attempt == task.retries:
                req_ms = int((time.time() - start_ts) * 1000)
                res = {"status": "error", "label": "提链失败", "error": err_msg, "req_ms": req_ms}
                db.update_registered_extract(
                    email=email,
                    extract_data={
                        "status": "failed",
                        "channel": task.channel,
                        "error": err_msg,
                        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                )
                task.mark_done(email, res)
            else:
                time.sleep(1.0)


def _worker_loop(task: ExtractJobTask, email_queue: queue.Queue) -> None:
    while not task.cancelled:
        try:
            email = email_queue.get_nowait()
        except queue.Empty:
            break
        try:
            _execute_account_extract(task, email)
        finally:
            email_queue.task_done()


def start_extract_job(emails: list[str], config: dict) -> str:
    unique_emails = list(dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()))
    if not unique_emails:
        raise ValueError("请提供至少一个要提炼的账号邮箱")

    task_id = str(uuid.uuid4())[:12]
    task = ExtractJobTask(task_id, unique_emails, config)

    with _tasks_lock:
        if len(_active_tasks) > 20:
            for k in list(_active_tasks.keys())[:-10]:
                _active_tasks.pop(k, None)
        _active_tasks[task_id] = task

    email_queue: queue.Queue = queue.Queue()
    for em in unique_emails:
        email_queue.put(em)

    def _run():
        with ThreadPoolExecutor(max_workers=task.workers, thread_name_prefix=f"extract_{task_id}") as pool:
            futures = [pool.submit(_worker_loop, task, email_queue) for _ in range(task.workers)]
            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    logger.warning(f"[extract_job] Worker 异常: {e}")

        task.finished_at = time.time()
        try:
            task.queue.put({"kind": "end", "task_id": task_id, "stats": task.stats})
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name=f"ExtractJobRunner-{task_id}")
    t.start()
    return task_id


def stop_extract_job(task_id: str) -> bool:
    with _tasks_lock:
        task = _active_tasks.get(task_id)
        if task:
            task.cancelled = True
            try:
                task.queue.put({"kind": "end", "task_id": task_id, "cancelled": True})
            except Exception:
                pass
            return True
    return False


def get_task_snapshot(task_id: str) -> Optional[dict]:
    with _tasks_lock:
        task = _active_tasks.get(task_id)
        if not task:
            return None
        with task._lock:
            return {
                "task_id": task.task_id,
                "channel": task.channel,
                "channel_name": task.channel_meta["name"],
                "exit_country": task.exit_country,
                "billing_country": task.billing_country,
                "currency": task.currency,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "cancelled": task.cancelled,
                "done_count": task.done_count,
                "total": len(task.items),
                "stats": dict(task.stats),
                "items": {k: dict(v) for k, v in task.items.items()},
            }


def get_task_queue(task_id: str) -> Optional[queue.Queue]:
    with _tasks_lock:
        task = _active_tasks.get(task_id)
        return task.queue if task else None


def get_task_logs(task_id: str, email: str) -> list[str]:
    with _tasks_lock:
        task = _active_tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            it = task.items.get(email.lower().strip())
            return list(it["logs"]) if it else []
