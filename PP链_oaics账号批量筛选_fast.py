#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone OAICS Checkout probe.

功能:
  1. 可选：用 Cloudflare trace 校验代理出口国家
  2. 创建 ChatGPT Checkout（custom 模式）
  3. 判断返回的 session id 是 oaics_ 还是 cs_

默认参数:
  账单国家  DE
  币种      EUR
  代理出口  BR
  UI 模式   custom
  促销      默认关闭（可用 --with-promo 开启）

依赖:
  pip install "curl_cffi>=0.15.0"

代理格式（每行一条，支持以下写法）:
  user:pass@host:port
  user:pass:host:port
  host:port:user:pass
  http://user:pass@host:port

  sticky 代理密码后缀可带国家/会话，例如:
  user:pass-BR-12345678-5m@host:port
  脚本会按 --proxy-country 自动改写国家段，并默认轮换 session。

AT 输入:
  --token "eyJ..."
  --token-file at.txt          # 纯 JWT，或含 accessToken 的 JSON

启动方法:
  # 基础
  python "PP链_oaics账号批量筛选_fast.py" --token-file at.txt --proxy-file proxies.txt

  # 多轮探测
  python "PP链_oaics账号批量筛选_fast.py" --token-file at.txt --proxy-file proxies.txt --rounds 5

  # 单条代理 + 跳过出口校验（更快）
  python "PP链_oaics账号批量筛选_fast.py" --token-file at.txt --proxy user:pass@host:port --skip-proxy-check

  # 创建时带促销，并输出 JSON
  python "PP链_oaics账号批量筛选_fast.py" --token-file at.txt --proxy-file proxies.txt --with-promo --out result.json

  # 并发
  python "PP链_oaics账号批量筛选_fast.py" --token-file at.txt --proxy-file proxies.txt --rounds 10 --workers 3

常用参数:
  --billing-country DE
  --currency EUR
  --proxy-country BR
  --rounds N
  --workers N
  --timeout 30
  --with-promo
  --skip-proxy-check
  --no-rotate
  --out result.json

退出码:
  0  至少一次命中 oaics_
  1  有响应但未命中 oaics_
  2  全部失败（网络/校验/HTTP 错误）
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:  # pragma: no cover
    CurlSession = None  # type: ignore[assignment]


CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
CLIENT_VERSION = "prod-db390ebea64862bf1899c420a4c736e0cf639747"
CLIENT_BUILD = "7904904"

DEFAULT_BILLING = "DE"
DEFAULT_CURRENCY = "EUR"
DEFAULT_PROXY_COUNTRY = "BR"


@dataclass
class ProbeResult:
    ok: bool
    state: str  # OAICS / CS / OAIC / NONE / ERROR / UNKNOWN
    session_id: str = ""
    session_id_masked: str = ""
    processor_entity: str = ""
    billing_country: str = DEFAULT_BILLING
    currency: str = DEFAULT_CURRENCY
    proxy_country: str = DEFAULT_PROXY_COUNTRY
    proxy: str = ""
    http_status: int = 0
    elapsed_ms: int = 0
    proxy_check_ms: int = 0
    checkout_ms: int = 0
    error: str = ""
    attempt: int = 1
    account_email: str = ""


def log(msg: str) -> None:
    print(msg, flush=True)


def mask(value: str, keep_start: int = 10, keep_end: int = 6) -> str:
    text = str(value or "")
    if len(text) <= keep_start + keep_end + 3:
        return text
    return f"{text[:keep_start]}***{text[-keep_end:]}"


def safe_close(session: Any) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def normalize_proxy_url(proxy: str) -> str:
    """Accept user:pass@host:port, user:pass:host:port, host:port:user:pass, http://..."""
    proxy = str(proxy or "").strip()
    if not proxy or proxy.startswith("#"):
        return ""
    if "://" in proxy:
        return proxy
    if "@" in proxy:
        return f"http://{proxy}"

    # user:pass:host:port  (password may contain -CC-session-ttl)
    raw_parts = proxy.rsplit(":", 2)
    if len(raw_parts) == 3 and raw_parts[2].isdigit() and ":" in raw_parts[0]:
        credentials, host, port = raw_parts
        username, password = credentials.split(":", 1)
        if username and password and host:
            return (
                f"http://{quote(username, safe='-._~')}:"
                f"{quote(password, safe='-._~')}@{host}:{port}"
            )

    # host:port:user:pass
    parts = proxy.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in proxy:
        host, port, username, password = parts
        return (
            f"http://{quote(username, safe='-._~')}:"
            f"{quote(password, safe='-._~')}@{host}:{port}"
        )
    return f"http://{proxy}"


def proxy_url_with_credentials(parsed: Any, username: str, password: str) -> str:
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def new_proxy_session_id() -> str:
    return str(random.randint(10_000_000, 99_999_999))


def route_proxy_country(proxy: str, country: str, session_id: str = "") -> str:
    """Rewrite sticky-proxy password suffix: PREFIX-CC-SESSION-TTL."""
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return proxy
    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    match = re.fullmatch(
        r"(?P<prefix>.+)-(?P<country>[A-Za-z]{2})-(?P<session>\d+)-(?P<ttl>\d+[A-Za-z]+)",
        password,
    )
    if not username or not parsed.hostname or match is None:
        return proxy
    routed_password = (
        f"{match.group('prefix')}-{country.upper()}-"
        f"{session_id or match.group('session')}-{match.group('ttl')}"
    )
    return proxy_url_with_credentials(parsed, username, routed_password)


def proxy_endpoint_label(proxy: str) -> str:
    try:
        parsed = urlsplit(normalize_proxy_url(proxy))
        host = parsed.hostname or "?"
        return f"{host}:{parsed.port}" if parsed.port else host
    except Exception:
        return "?"


def set_proxy(session: Any, proxy: str) -> None:
    proxy = normalize_proxy_url(proxy)
    session.proxies = {"http": proxy, "https": proxy} if proxy else {}


def load_token(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
            for key in ("accessToken", "access_token", "token"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception:
            pass
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    for line in text.splitlines():
        line = line.strip()
        if line.count(".") >= 2 and len(line) > 40:
            return line
    return text


def account_email_from_token(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return ""
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        profile = payload.get("https://api.openai.com/profile") or {}
        if isinstance(profile, dict):
            return str(profile.get("email") or "")
    except Exception:
        pass
    return ""


def parse_proxy_pool(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        proxy = normalize_proxy_url(item)
        if not proxy or proxy in seen:
            continue
        seen.add(proxy)
        out.append(proxy)
    if not out:
        raise SystemExit("proxy pool is empty")
    return out


def classify(session_id: str) -> str:
    text = str(session_id or "").strip()
    if text.startswith("oaics_"):
        return "OAICS"
    if text.startswith("cs_"):
        return "CS"
    if text.startswith("oaic_"):
        return "OAIC"
    if not text:
        return "NONE"
    return "UNKNOWN"


def extract_session_id(payload: dict[str, Any]) -> str:
    for key in ("checkout_session_id", "session_id", "id"):
        text = str(payload.get(key) or "").strip()
        if text.startswith(("oaics_", "oaic_", "cs_")):
            return text
    blob = json.dumps(payload, ensure_ascii=False)
    for pattern in (
        r"\boaics_[A-Za-z0-9_-]+\b",
        r"\boaic_[A-Za-z0-9_-]+\b",
        r"\bcs_(?:live|test)?_[A-Za-z0-9_-]+\b",
    ):
        match = re.search(pattern, blob)
        if match:
            return match.group(0)
    return ""


def extract_processor(payload: dict[str, Any]) -> str:
    for key in ("processor_entity", "processorEntity", "processor"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def new_session(token: str, proxy: str, device_id: str, session_id: str) -> Any:
    if CurlSession is None:
        raise SystemExit('curl_cffi is required: pip install "curl_cffi>=0.15.0"')
    session = CurlSession(impersonate="chrome136")
    if hasattr(session, "trust_env"):
        session.trust_env = False
    session.headers.update(
        {
            "User-Agent": DEFAULT_UA,
            "Accept": "*/*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Authorization": f"Bearer {token}",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Content-Type": "application/json",
            "oai-device-id": device_id,
            "oai-language": "de-DE",
            "oai-session-id": session_id,
            "oai-client-version": CLIENT_VERSION,
            "oai-client-build-number": CLIENT_BUILD,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Cookie": f"oai-did={device_id}",
        }
    )
    set_proxy(session, proxy)
    return session


def probe_proxy_country(proxy: str, expected: str, timeout: float = 8.0) -> tuple[str, int]:
    started = time.time()
    session = CurlSession(impersonate="chrome136")
    try:
        if hasattr(session, "trust_env"):
            session.trust_env = False
        set_proxy(session, proxy)
        response = session.get(TRACE_URL, timeout=timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise RuntimeError(f"trace HTTP {status}")
        fields = dict(
            line.split("=", 1)
            for line in str(getattr(response, "text", "") or "").splitlines()
            if "=" in line
        )
        observed = str(fields.get("loc") or "").upper()
        if expected and observed != expected.upper():
            raise RuntimeError(
                f"proxy country mismatch: expected {expected.upper()}, observed {observed or 'unknown'}"
            )
        return observed, int((time.time() - started) * 1000)
    finally:
        safe_close(session)


def create_checkout(
    session: Any,
    *,
    billing_country: str,
    currency: str,
    with_promo: bool,
    timeout: float,
) -> tuple[dict[str, Any], int]:
    body: dict[str, Any] = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": billing_country,
            "currency": currency,
        },
        "checkout_ui_mode": "custom",
    }
    if with_promo:
        body["promo_campaign"] = {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        }
    response = session.post(
        CHECKOUT_URL,
        json=body,
        headers={
            "Referer": "https://chatgpt.com/",
            "x-openai-target-path": "/backend-api/payments/checkout",
            "x-openai-target-route": "/backend-api/payments/checkout",
        },
        timeout=timeout,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    try:
        payload = response.json() or {}
    except Exception:
        payload = {"_raw": (getattr(response, "text", "") or "")[:500]}
    if not isinstance(payload, dict):
        payload = {"_non_object": payload}
    return payload, status


def probe_once(
    token: str,
    proxy: str,
    *,
    billing_country: str = DEFAULT_BILLING,
    currency: str = DEFAULT_CURRENCY,
    proxy_country: str = DEFAULT_PROXY_COUNTRY,
    with_promo: bool = False,
    skip_proxy_check: bool = False,
    rotate_session: bool = True,
    timeout: float = 30.0,
    attempt: int = 1,
    account_email: str = "",
) -> ProbeResult:
    started = time.time()
    if rotate_session:
        routed = route_proxy_country(proxy, proxy_country, new_proxy_session_id())
    else:
        routed = route_proxy_country(proxy, proxy_country)
    if not routed:
        routed = normalize_proxy_url(proxy)

    result = ProbeResult(
        ok=False,
        state="ERROR",
        billing_country=billing_country,
        currency=currency,
        proxy_country=proxy_country,
        proxy=proxy_endpoint_label(routed),
        attempt=attempt,
        account_email=account_email,
    )

    session = None
    try:
        if not skip_proxy_check:
            t0 = time.time()
            observed, _ = probe_proxy_country(routed, proxy_country, timeout=min(timeout, 8.0))
            result.proxy_check_ms = int((time.time() - t0) * 1000)
            log(f"[#{attempt}] proxy ok country={observed} {result.proxy_check_ms}ms")

        device_id = str(uuid.uuid4())
        oai_sid = str(uuid.uuid4())
        session = new_session(token, routed, device_id, oai_sid)

        t1 = time.time()
        payload, status = create_checkout(
            session,
            billing_country=billing_country,
            currency=currency,
            with_promo=with_promo,
            timeout=timeout,
        )
        result.checkout_ms = int((time.time() - t1) * 1000)
        result.http_status = status

        if status >= 400:
            detail = ""
            if isinstance(payload, dict):
                detail = str(
                    payload.get("detail")
                    or payload.get("error")
                    or payload.get("_raw")
                    or ""
                )
            result.error = f"HTTP {status}: {detail[:200]}"
            return result

        session_id = extract_session_id(payload)
        state = classify(session_id)
        result.session_id = session_id
        result.session_id_masked = mask(session_id)
        result.state = state
        result.processor_entity = extract_processor(payload)
        result.ok = state == "OAICS"
        if not result.ok:
            result.error = (
                f"oaics_ not found (state={state}, "
                f"sid={result.session_id_masked or 'EMPTY'})"
            )
        return result
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.state = "ERROR"
        result.ok = False
        return result
    finally:
        safe_close(session)
        result.elapsed_ms = int((time.time() - started) * 1000)


def summarize(
    results: list[ProbeResult],
    *,
    billing_country: str,
    currency: str,
    proxy_country: str,
) -> dict[str, Any]:
    total = len(results)
    states: dict[str, int] = {}
    for item in results:
        states[item.state] = states.get(item.state, 0) + 1
    hits = states.get("OAICS", 0)
    elapsed = [item.elapsed_ms for item in results if item.elapsed_ms > 0]
    return {
        "total": total,
        "oaics_hits": hits,
        "oaics_rate": round(hits / total, 4) if total else 0.0,
        "states": states,
        "avg_ms": int(sum(elapsed) / len(elapsed)) if elapsed else 0,
        "min_ms": min(elapsed) if elapsed else 0,
        "max_ms": max(elapsed) if elapsed else 0,
        "billing_country": billing_country,
        "currency": currency,
        "proxy_country": proxy_country,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Standalone probe: does Checkout return oaicS session id?"
    )
    parser.add_argument("--token", default="", help="ChatGPT access token")
    parser.add_argument("--token-file", default="", help="file containing access token")
    parser.add_argument("--proxy", default="", help="single proxy")
    parser.add_argument("--proxy-file", default="", help="proxy pool file, one per line")
    parser.add_argument("--billing-country", default=DEFAULT_BILLING, help="billing country, default DE")
    parser.add_argument("--currency", default=DEFAULT_CURRENCY, help="currency, default EUR")
    parser.add_argument("--proxy-country", default=DEFAULT_PROXY_COUNTRY, help="proxy exit country, default BR")
    parser.add_argument("--rounds", type=int, default=1, help="probe rounds")
    parser.add_argument("--workers", type=int, default=1, help="concurrency")
    parser.add_argument("--timeout", type=float, default=30.0, help="request timeout seconds")
    parser.add_argument(
        "--with-promo",
        action="store_true",
        help="attach plus-1-month-free on create (off by default)",
    )
    parser.add_argument(
        "--skip-proxy-check",
        action="store_true",
        help="skip Cloudflare trace exit-country check",
    )
    parser.add_argument(
        "--no-rotate",
        action="store_true",
        help="do not rotate sticky proxy session id",
    )
    parser.add_argument("--out", default="", help="write JSON report to path")
    args = parser.parse_args(argv)

    token_raw = args.token
    if args.token_file:
        token_raw = Path(args.token_file).read_text(encoding="utf-8", errors="replace")
    token = load_token(token_raw)
    if not token:
        raise SystemExit("missing access token (--token / --token-file)")

    proxy_raw = args.proxy
    if args.proxy_file:
        proxy_raw = Path(args.proxy_file).read_text(encoding="utf-8", errors="replace")
    if not str(proxy_raw or "").strip():
        raise SystemExit("missing proxy (--proxy / --proxy-file)")
    proxies = parse_proxy_pool(proxy_raw)

    rounds = max(1, int(args.rounds or 1))
    workers = max(1, int(args.workers or 1))
    billing = str(args.billing_country or DEFAULT_BILLING).upper()
    currency = str(args.currency or DEFAULT_CURRENCY).upper()
    proxy_country = str(args.proxy_country or DEFAULT_PROXY_COUNTRY).upper()
    email = account_email_from_token(token)

    log(
        f"[probe] email={email or '?'} {billing}/{currency} "
        f"proxy={proxy_country} promo={'yes' if args.with_promo else 'no'} "
        f"rounds={rounds} workers={workers} proxies={len(proxies)} "
        f"proxy_check={'off' if args.skip_proxy_check else 'cf-trace'}"
    )

    jobs = [(i + 1, proxies[i % len(proxies)]) for i in range(rounds)]
    results: list[ProbeResult] = []

    def run(job: tuple[int, str]) -> ProbeResult:
        attempt, proxy = job
        log(f"[#{attempt}] start {proxy_endpoint_label(proxy)}")
        item = probe_once(
            token,
            proxy,
            billing_country=billing,
            currency=currency,
            proxy_country=proxy_country,
            with_promo=bool(args.with_promo),
            skip_proxy_check=bool(args.skip_proxy_check),
            rotate_session=not bool(args.no_rotate),
            timeout=float(args.timeout),
            attempt=attempt,
            account_email=email,
        )
        mark = "HIT" if item.ok else "MISS"
        log(
            f"[#{attempt}] {mark} state={item.state} sid={item.session_id_masked or '-'} "
            f"http={item.http_status} total={item.elapsed_ms}ms "
            f"(proxy={item.proxy_check_ms}ms checkout={item.checkout_ms}ms)"
            + (f" err={item.error}" if item.error and not item.ok else "")
        )
        return item

    if workers == 1:
        for job in jobs:
            results.append(run(job))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, job) for job in jobs]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: item.attempt)

    summary = summarize(
        results,
        billing_country=billing,
        currency=currency,
        proxy_country=proxy_country,
    )
    report = {"summary": summary, "results": [asdict(item) for item in results]}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text, flush=True)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        log(f"[probe] wrote {args.out}")

    if summary["oaics_hits"] > 0:
        return 0
    if summary["total"] and summary["states"].get("ERROR", 0) == summary["total"]:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
